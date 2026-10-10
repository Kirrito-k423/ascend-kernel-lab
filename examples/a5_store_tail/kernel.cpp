#include "kernel_operator.h"
using namespace AscendC;

template<bool Store, bool Set>
__aicore__ inline void WindowEvent(uint32_t bank) {
    const auto e = bank ? EVENT_ID1 : EVENT_ID0;
    if constexpr (Store) {
        if constexpr (Set) SetFlag<HardEvent::MTE3_S>(e);
        else WaitFlag<HardEvent::MTE3_S>(e);
    } else {
        if constexpr (Set) SetFlag<HardEvent::MTE2_S>(e);
        else WaitFlag<HardEvent::MTE2_S>(e);
    }
}

template<bool Store, bool TailOnly = false>
__aicore__ inline void Work(GM_ADDR input, GM_ADDR output, GM_ADDR records,
    uint32_t sharedRead, uint32_t tile, uint32_t batch, uint64_t ringPerCore,
    uint32_t groups, uint32_t control, uint32_t stamp) {
    const uint32_t core = GetBlockIdx();
    const uint32_t bankBytes = tile * batch;
    TPipe pipe;
    TBuf<TPosition::VECCALC> dataBuf, timerBuf;
    pipe.InitBuffer(timerBuf, 32);
    pipe.InitBuffer(dataBuf, 2 * bankBytes);
    auto data = dataBuf.Get<uint32_t>(); auto time = timerBuf.Get<uint64_t>();
    GlobalTensor<uint32_t> x, y; GlobalTensor<uint64_t> ticks;
    x.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(input));
    y.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(output));
    ticks.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(records));
    if constexpr (Store) {
        Duplicate(data, (0x9e3779b9u * (core + 1)) ^ stamp, 2 * bankBytes / 4);
        SetFlag<HardEvent::V_MTE3>(EVENT_ID0); WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    } else if (control) {
        Duplicate(data, uint32_t(0), 2 * bankBytes / 4);
        SetFlag<HardEvent::V_MTE3>(EVENT_ID0); WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    }
    // 同步参与本次 launch 的 AIV；没有超过实际可用 AIV 的逻辑 block。
    SyncAll();
    const uint64_t begin = GetSystemCycle();
    const uint64_t base = sharedRead ? 0 : uint64_t(core) * ringPerCore;
    uint64_t cursor = 0;
    for (uint32_t i = 0; i < groups; ++i) {
        const uint32_t bank = i & 1;
        if constexpr (!TailOnly) {
            if (i >= 2) WindowEvent<Store, false>(bank);
        }
        for (uint32_t j = 0; j < batch; ++j) {
            if (!control) {
                auto local = data[(bank * bankBytes + j * tile) / 4];
                if constexpr (Store) DataCopy(y[(base + cursor + j * tile) / 4], local, tile / 4);
                else DataCopy(local, x[(base + cursor + j * tile) / 4], tile / 4);
            } else asm volatile("" ::: "memory");
        }
        if constexpr (!TailOnly) WindowEvent<Store, true>(bank);
        cursor += bankBytes;
        if (cursor == ringPerCore) cursor = 0;
    }
    if constexpr (TailOnly) {
        // 此变体只用于纯写：Duplicate 后源 UB 不再变化，所有请求只读它。
        // 循环中不设置/消费事件，末尾一次 MTE3_S 排空全部写请求。
        // 不能仅删除 WaitFlag 而保留循环 SetFlag：重复设置未消费事件不合法。
        SetFlag<HardEvent::MTE3_S>(EVENT_ID0); WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    } else {
        WindowEvent<Store, false>(0); WindowEvent<Store, false>(1);
    }
    const uint64_t end = GetSystemCycle();
    // 先让所有核结束主体，再导出。导出不会与其他核的被测 DMA 竞争。
    SyncAll();
    if constexpr (!Store) {
        SetFlag<HardEvent::MTE2_MTE3>(EVENT_ID0); WaitFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
        DataCopy(y[uint64_t(core) * 2 * bankBytes / 4], data, 2 * bankBytes / 4);
    }
    time.SetValue(0, begin); time.SetValue(1, end);
    time.SetValue(2, core); time.SetValue(3, 0x414b4c42414e4431ULL);
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0); WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopy(ticks[core * 4], time, 4);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0); WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}

extern "C" __global__ __aicore__ void bandwidth_kernel(GM_ADDR input, GM_ADDR output,
    GM_ADDR records, uint32_t direction, uint32_t sharedRead, uint32_t tile,
    uint32_t batch, uint64_t ringPerCore, uint32_t groups, uint32_t control, uint32_t stamp, uint32_t tailOnly) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    if (tailOnly) Work<true, true>(input, output, records, sharedRead, tile, batch, ringPerCore, groups, control, stamp);
    else if (direction) Work<true>(input, output, records, sharedRead, tile, batch, ringPerCore, groups, control, stamp);
    else Work<false>(input, output, records, sharedRead, tile, batch, ringPerCore, groups, control, stamp);
}

extern "C" void launch_bandwidth(void* stream, void* input, void* output, void* records,
    uint32_t cores, uint32_t direction, uint32_t sharedRead, uint32_t tile, uint32_t batch,
    uint64_t ringPerCore, uint32_t groups, uint32_t control, uint32_t stamp, uint32_t tailOnly) {
    bandwidth_kernel<<<cores, 2 * tile * batch + 32, stream>>>(static_cast<uint8_t*>(input),
        static_cast<uint8_t*>(output), static_cast<uint8_t*>(records), direction, sharedRead,
        tile, batch, ringPerCore, groups, control, stamp, tailOnly);
}

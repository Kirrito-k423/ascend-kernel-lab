#include "kernel_operator.h"
#include "akl/datacopy_params.h"
#include "akl/trace/recorder.h"
using namespace AscendC;

template<bool Store>
__aicore__ inline void Complete() {
    if constexpr (Store) {
        SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    } else {
        SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
    }
}

template<typename T, bool Store>
__aicore__ inline void CopyOne(LocalTensor<T> ub, GlobalTensor<T> gm, const akl::CopyParams& p) {
    if (p.api == 0) {
        if constexpr (Store) DataCopy(gm, ub, p.block_bytes / sizeof(T));
        else DataCopy(ub, gm, p.block_bytes / sizeof(T));
    } else if (p.api == 1) {
        DataCopyParams cp{static_cast<uint16_t>(p.blocks), static_cast<uint16_t>(p.block_bytes / 32),
            static_cast<uint16_t>(Store ? 0 : p.gm_gap_bytes / 32),
            static_cast<uint16_t>(Store ? p.gm_gap_bytes / 32 : 0)};
        if constexpr (Store) DataCopy(gm, ub, cp);
        else DataCopy(ub, gm, cp);
    } else {
        // DataCopyPad(DataCopyParams) 的 blockLen/GM stride 单位是字节。
        DataCopyParams cp{static_cast<uint16_t>(p.blocks), static_cast<uint16_t>(p.block_bytes),
            static_cast<uint16_t>(Store ? 0 : p.gm_gap_bytes),
            static_cast<uint16_t>(Store ? p.gm_gap_bytes : 0)};
        if constexpr (Store) DataCopyPad(gm, ub, cp);
        else {
            DataCopyPadParams pad{false, 0, 0, 0};
            DataCopyPad(ub, gm, cp, pad);
        }
    }
}

template<typename T, bool Store, bool Trace>
__aicore__ inline void Run(GM_ADDR x, GM_ADDR y, GM_ADDR record, const akl::CopyParams& p) {
    akl::Recorder<Trace> trace;
    trace.Mark(0);
    TPipe pipe;
    TBuf<TPosition::VECCALC> dataBuf, traceBuf;
    const uint32_t ubBytes = p.batch * p.ub_stride_bytes;
    pipe.InitBuffer(dataBuf, ubBytes);
    if constexpr (Trace) pipe.InitBuffer(traceBuf, akl::kWords * sizeof(uint64_t));
    auto data = dataBuf.Get<T>();
    GlobalTensor<T> input, output;
    input.SetGlobalBuffer(reinterpret_cast<__gm__ T*>(x));
    output.SetGlobalBuffer(reinterpret_cast<__gm__ T*>(y));
    if constexpr (Store) {
        DataCopy(data, input, ubBytes / sizeof(T));
        SetFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
    } else {
        // 初始化整个 UB，随后只校验定义了语义的有效 payload。
        for (uint32_t i = 0; i < ubBytes / sizeof(T); ++i) data.SetValue(i, T(0));
        SetFlag<HardEvent::S_MTE2>(EVENT_ID0);
        WaitFlag<HardEvent::S_MTE2>(EVENT_ID0);
    }
    Complete<Store>();
    trace.Mark(1);
    trace.Mark(2);
    for (uint32_t group = 0; group < p.loops; ++group) {
        for (uint32_t j = 0; j < p.batch; ++j) {
            if (p.control == 0) {
                const uint32_t slot = (group * p.batch + j) % p.slots;
                auto ub = data[(j * p.ub_stride_bytes + p.ub_offset_bytes) / sizeof(T)];
                if constexpr (Store) CopyOne<T, true>(ub, output[(slot * p.gm_stride_bytes + p.gm_offset_bytes) / sizeof(T)], p);
                else CopyOne<T, false>(ub, input[(slot * p.gm_stride_bytes + p.gm_offset_bytes) / sizeof(T)], p);
            } else asm volatile("" ::: "memory");
        }
        if (p.control != 2) Complete<Store>();
    }
    trace.Mark(3);
    if constexpr (!Store) {
        if (p.control == 0) {
            SetFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
            WaitFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
            DataCopy(output, data, ubBytes / sizeof(T));
        }
    }
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    trace.Mark(4);
    if constexpr (Trace) trace.Flush(record, traceBuf.Get<uint64_t>(), p.control == 0 ? p.block_bytes * p.blocks * p.batch : 0);
}

template<bool Trace>
__aicore__ inline void Entry(GM_ADDR x, GM_ADDR y, GM_ADDR record, GM_ADDR config) {
    akl::CopyParams p;
    auto dst = reinterpret_cast<uint32_t*>(&p);
    auto src = reinterpret_cast<__gm__ uint32_t*>(config);
    for (uint32_t i = 0; i < sizeof(p) / 4; ++i) dst[i] = src[i];
    // 显式实例化真实数据类型；不把 BF16/FP16 的数值运算用于拷贝 oracle。
    if (p.reserved0 == 1) {
        if (p.direction) Run<bfloat16_t, true, Trace>(x, y, record, p);
        else Run<bfloat16_t, false, Trace>(x, y, record, p);
    } else if (p.reserved0 == 2) {
        if (p.direction) Run<float, true, Trace>(x, y, record, p);
        else Run<float, false, Trace>(x, y, record, p);
    } else if (p.element_bytes == 1) {
        if (p.direction) Run<uint8_t, true, Trace>(x, y, record, p);
        else Run<uint8_t, false, Trace>(x, y, record, p);
    } else if (p.element_bytes == 2) {
        if (p.direction) Run<half, true, Trace>(x, y, record, p);
        else Run<half, false, Trace>(x, y, record, p);
    } else {
        if (p.direction) Run<uint32_t, true, Trace>(x, y, record, p);
        else Run<uint32_t, false, Trace>(x, y, record, p);
    }
}
extern "C" __global__ __aicore__ void akl_copy_trace(GM_ADDR x, GM_ADDR y, GM_ADDR record, GM_ADDR p) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    Entry<true>(x, y, record, p);
}
extern "C" __global__ __aicore__ void akl_copy_plain(GM_ADDR x, GM_ADDR y, GM_ADDR record, GM_ADDR p) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    Entry<false>(x, y, record, p);
}
#ifndef ASCENDC_CPU_DEBUG
extern "C" uint32_t akl_copy_params_size() { return sizeof(akl::CopyParams); }
extern "C" void akl_copy_launch(uint32_t blocks, void* stream, void* x, void* unused,
                                void* y, void* record, void* p, int trace) {
    if (trace) akl_copy_trace<<<blocks, nullptr, stream>>>(static_cast<uint8_t*>(x), static_cast<uint8_t*>(y), static_cast<uint8_t*>(record), static_cast<uint8_t*>(p));
    else akl_copy_plain<<<blocks, nullptr, stream>>>(static_cast<uint8_t*>(x), static_cast<uint8_t*>(y), static_cast<uint8_t*>(record), static_cast<uint8_t*>(p));
}
#endif

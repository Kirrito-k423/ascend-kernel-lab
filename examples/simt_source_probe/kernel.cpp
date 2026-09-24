#include "kernel_operator.h"
#include "simt_api/asc_simt.h"
using namespace AscendC;

// 合成索引追踪：每次 GM 读取依赖前一次结果，模拟上下文间接访存的形态。
__simt_vf__ __aicore__ LAUNCH_BOUND(32) inline void ProbeContexts(
    __gm__ uint32_t* table, __ubuf__ uint32_t* sink, uint32_t steps) {
    uint32_t pos = threadIdx.x;
    uint32_t checksum = pos;
    for (uint32_t i = 0; i < steps; ++i) {
        pos = table[pos];
        // uint32 溢出按模 2^32 定义；结果落盘并由 Host 校验，不能成为死代码。
        checksum = checksum * 1664525U + pos + 1013904223U;
    }
    sink[threadIdx.x] = checksum;
}

extern "C" __global__ __aicore__ void akl_simt_probe_kernel(
    GM_ADDR table, GM_ADDR output, uint32_t steps) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    TPipe pipe;
    TBuf<TPosition::VECCALC> buffer;
    pipe.InitBuffer(buffer, 32 * sizeof(uint32_t));
    auto values = buffer.Get<uint32_t>();
    Simt::VF_CALL<ProbeContexts>(Simt::Dim3{32, 1, 1},
        reinterpret_cast<__gm__ uint32_t*>(table),
        reinterpret_cast<__ubuf__ uint32_t*>(values.GetPhyAddr()), steps);
    // 仅在 VF 的 UB 结果交给 MTE3 时同步，不在被测循环中逐行串行化。
    SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
    WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
    GlobalTensor<uint32_t> dst;
    dst.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(output));
    DataCopy(dst, values, 32);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}

extern "C" void launch_probe(void* stream, void* table, void* output, uint32_t steps) {
    // 首个能力探针明确只启动一个逻辑 AIV，不代表设备总核数。
    akl_simt_probe_kernel<<<1, nullptr, stream>>>(
        static_cast<uint8_t*>(table), static_cast<uint8_t*>(output), steps);
}

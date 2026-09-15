#include "kernel_operator.h"
#include "akl/params.h"
#include "akl/trace/recorder.h"
using namespace AscendC;

template<bool Trace>
__aicore__ inline void Micro(GM_ADDR x, GM_ADDR mask, GM_ADDR y, GM_ADDR record, GM_ADDR config) {
    akl::Recorder<Trace> trace;
    trace.Mark(0); // 首个可观测入口，在参数读取和 TPipe 初始化之前。
    akl::Params p;
    auto sourceParams = reinterpret_cast<__gm__ uint32_t*>(config);
    auto localParams = reinterpret_cast<uint32_t*>(&p);
    for (uint32_t i = 0; i < sizeof(p) / sizeof(uint32_t); ++i) localParams[i] = sourceParams[i];
    const uint32_t core = GetBlockIdx();
    TPipe pipe;
    TBuf<TPosition::VECCALC> srcBuf, dstBuf, maskBuf, traceBuf;
    pipe.InitBuffer(srcBuf, p.output_stride * sizeof(uint32_t));
    pipe.InitBuffer(dstBuf, p.output_stride * sizeof(uint32_t));
    pipe.InitBuffer(maskBuf, 4096);
    if constexpr (Trace) pipe.InitBuffer(traceBuf, akl::kWords * sizeof(uint64_t));
    auto src = srcBuf.Get<uint32_t>();
    auto dst = dstBuf.Get<uint32_t>();
    auto pattern = maskBuf.Get<uint32_t>();
    GlobalTensor<uint32_t> xGm, yGm, maskGm;
    xGm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(x) + core * p.input_stride);
    yGm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(y) + core * p.output_stride);
    maskGm.SetGlobalBuffer(reinterpret_cast<__gm__ uint32_t*>(mask));
    uint64_t retained = 0;
    if (p.op == 2 || p.op == 4) {
        DataCopy(src, xGm, p.op == 4 ? p.input_stride : p.output_stride);
        DataCopy(pattern, maskGm, 1024);
        SetFlag<HardEvent::MTE2_V>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_V>(EVENT_ID0);
        SetFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
    }
    // 故意延迟用于检查每核时间差是否被导出保留，不证明跨核时钟已校准。
    if (p.delay_ticks) {
        const uint64_t start = GetSystemCycle();
        const uint64_t delay = uint64_t(core) * p.delay_ticks;
        while (uint64_t(GetSystemCycle()) - start < delay) {}
    }
    trace.Mark(1);
    trace.Mark(2);
    for (uint32_t i = 0; i < p.loops; ++i) {
        if (p.op == 1) {
            DataCopyParams cp{static_cast<uint16_t>(p.block_count),
                              static_cast<uint16_t>(p.payload_bytes / 32),
                              static_cast<uint16_t>(p.gap_bytes / 32), 0};
            DataCopy(src, xGm, cp);
            SetFlag<HardEvent::MTE2_S>(EVENT_ID0);
            WaitFlag<HardEvent::MTE2_S>(EVENT_ID0);
        } else if (p.op == 4) {
            DataCopyParams cp{static_cast<uint16_t>(p.block_count),
                              static_cast<uint16_t>(p.payload_bytes / 32), 0,
                              static_cast<uint16_t>(p.gap_bytes / 32)};
            DataCopy(yGm, src, cp);
            SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
            WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
        } else if (p.op == 2) {
            GatherMaskParams gp{static_cast<uint8_t>(p.src0_block_stride),
                                static_cast<uint16_t>(p.repeats),
                                static_cast<uint16_t>(p.src0_repeat_stride),
                                static_cast<uint8_t>(p.src1_repeat_stride)};
            if (p.pattern) GatherMask(dst, src, static_cast<uint8_t>(p.pattern),
                                      bool(p.reduce), p.reduce ? p.elements : 0, gp, retained);
            else GatherMask(dst, src, pattern, bool(p.reduce), p.reduce ? p.elements : 0, gp, retained);
            SetFlag<HardEvent::V_S>(EVENT_ID0);
            WaitFlag<HardEvent::V_S>(EVENT_ID0);
        } else {
            // 空循环含编译器屏障，作为计时与循环控制对照。
            asm volatile("" ::: "memory");
        }
    }
    trace.Mark(3);
    if (p.op == 4) retained = p.payload_bytes / 4 * p.block_count;
    if (p.op == 1) {
        retained = p.payload_bytes / 4 * p.block_count;
        SetFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::MTE2_MTE3>(EVENT_ID0);
        DataCopy(yGm, src, retained);
    } else if (p.op == 2) {
        SetFlag<HardEvent::V_MTE3>(EVENT_ID0);
        WaitFlag<HardEvent::V_MTE3>(EVENT_ID0);
        // 只验证有效输出前缀；对齐搬出部分不赋予语义。
        const uint32_t copyCount = (static_cast<uint32_t>(retained) + 7) / 8 * 8;
        if (copyCount) DataCopy(yGm, dst, copyCount);
    }
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);
    WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    trace.Mark(4);
    if constexpr (Trace) trace.Flush(record, traceBuf.Get<uint64_t>(), retained);
}

extern "C" __global__ __aicore__ void akl_trace_kernel(GM_ADDR x, GM_ADDR mask, GM_ADDR y, GM_ADDR record, GM_ADDR p) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    Micro<true>(x, mask, y, record, p);
}
extern "C" __global__ __aicore__ void akl_plain_kernel(GM_ADDR x, GM_ADDR mask, GM_ADDR y, GM_ADDR record, GM_ADDR p) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    Micro<false>(x, mask, y, record, p);
}
#ifndef ASCENDC_CPU_DEBUG
extern "C" void akl_launch(uint32_t blocks, void* stream, void* x, void* mask, void* y, void* record, void* p, int trace) {
    if (trace) akl_trace_kernel<<<blocks, nullptr, stream>>>(
        static_cast<uint8_t*>(x), static_cast<uint8_t*>(mask), static_cast<uint8_t*>(y),
        static_cast<uint8_t*>(record), static_cast<uint8_t*>(p));
    else akl_plain_kernel<<<blocks, nullptr, stream>>>(
        static_cast<uint8_t*>(x), static_cast<uint8_t*>(mask), static_cast<uint8_t*>(y),
        static_cast<uint8_t*>(record), static_cast<uint8_t*>(p));
}
extern "C" uint32_t akl_params_size() { return sizeof(akl::Params); }
#endif

// 真实 CANN 编译用例；编译成功不代表 NPU 实测。
#include "akl/trace/semantic.h"
extern "C" [[bisheng::core_ratio(0, 1)]] __global__ __aicore__ void quantity_trace_compile(GM_ADDR output, uint32_t count) {
    akl::Recorder<true, 8, true> clock;
    AKL_DEBUG_CLOCK(clock, "test", "begin");
    AKL_DEBUG_CLOCK(clock, "test", "integer", count, "WQE");
    AKL_DEBUG_CLOCK(clock, "test", "fraction", .5f, "GB");
    akl::Recorder<false, 8, true> disabled;
    AKL_DEBUG_CLOCK(disabled, "disabled", count, "items");
    AscendC::TPipe pipe;
    AscendC::TBuf<AscendC::TPosition::VECCALC> buffer;
    pipe.InitBuffer(buffer, (8 + 4 * 8) * sizeof(uint64_t));
    clock.Flush(output, buffer.Get<uint64_t>(), 0);
}

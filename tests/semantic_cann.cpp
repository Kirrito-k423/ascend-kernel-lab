// CANN 9.1/Bisheng 编译回归；不执行 NPU kernel。A3 用 dav-2201，A5 用 dav-3510。
// source /usr/local/Ascend/cann/set_env.sh
// bisheng -xasc --npu-arch=dav-3510 -std=c++17 -O2 -Iinclude -c tests/semantic_cann.cpp -o /tmp/semantic_cann.o
#include "akl/trace/semantic.h"
extern "C" [[bisheng::core_ratio(0, 1)]] __global__ __aicore__ void semantic_trace_compile(GM_ADDR output) {
    akl::Recorder<true, 8> clock;
    AKL_DEBUG_CLOCK(clock, "big func");
    AKL_DEBUG_CLOCK(clock, "big func", "sub func");
    for (int i = 0; i < 3; ++i)
        AKL_DEBUG_CLOCK(clock, "big func", "sub func", "A part", "iteration");
    static_assert(akl::PathHash(2166136261u, "big func", "sub func", "A part", "iteration") == 2279751878u);
    static_assert(akl::PathHash(2166136261u, "ab", "c") != akl::PathHash(2166136261u, "a", "bc"));
    akl::Recorder<false, 8> disabled;
    AKL_DEBUG_CLOCK(disabled, "disabled");
    AscendC::TPipe pipe;
    AscendC::TBuf<AscendC::TPosition::VECCALC> buffer;
    pipe.InitBuffer(buffer, (8 + 2 * 8) * sizeof(uint64_t));
    clock.Flush(output, buffer.Get<uint64_t>(), 0);
}

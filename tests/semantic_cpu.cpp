// CPU API 替身：验证记录协议和模板行为，不模拟任何 NPU 流水或性能。
#include "akl/trace/semantic.h"
#include "akl/trace/capture.h"
#include <cassert>
#include <iostream>
#define DebugClock(...) AKL_DEBUG_CLOCK(recorder, __VA_ARGS__)
int main(int argc, char** argv) {
    assert(argc == 2);
    akl::Capture<8> capture(2, nullptr);
    for (AscendC::block = 0; AscendC::block < 2; ++AscendC::block) {
        akl::Recorder<true, 8> recorder;
        DebugClock("big func", "sub func", "A part", "entry");
        int i = 0;
        while (i++ < 5) DebugClock("big func", "sub func", "A part", "iteration");
        DebugClock("big func", "sub func", "A part", "a part end");
        uint64_t scratch[akl::Capture<8>::words];
        recorder.Flush(capture.Data(), {scratch}, 0);
        assert(scratch[2] == 7 && scratch[3] == 0);
    }
    capture.Export(argv[1], 0);
    AscendC::block = 0;
    akl::Capture<4> truncated(1, nullptr);
    akl::Recorder<true, 4> recorder;
    for (int i = 0; i < 7; ++i) DebugClock("big func", "sub func", "A part", "iteration");
    uint64_t scratch[akl::Capture<4>::words];
    recorder.Flush(truncated.Data(), {scratch}, 0);
    assert(scratch[2] == 4 && scratch[3] == 3);
    truncated.Export(argv[1], 1);
    akl::Recorder<false, 4> off;
    const auto before = AscendC::tick;
    AKL_DEBUG_CLOCK(off, "disabled");
    off.Flush(nullptr, {nullptr}, 0);
    assert(AscendC::tick == before);
    static_assert(akl::PathHash(2166136261u, "ab", "c") != akl::PathHash(2166136261u, "a", "bc"));
    std::cout << "CPU stub: 2 blocks x 7 events; loop occurrence=5; capacity=4 dropped=3; disabled no clock reads\n";
    std::cout << "iteration id=" << akl::PathHash(2166136261u,"big func","sub func","A part","iteration") << '\n';
}

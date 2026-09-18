#include "akl/trace/semantic.h"
#include "akl/trace/capture.h"
#include <cassert>
int main(int argc,char**argv) {
    assert(argc==2);
    akl::Capture<8,true> capture(1,nullptr);
    akl::Recorder<true,8,true> recorder;
    AKL_DEBUG_CLOCK(recorder,"test","begin");
    AKL_DEBUG_CLOCK(recorder,"test","integer",uint64_t(9007199254740993ULL),"items");
    AKL_DEBUG_CLOCK(recorder,"test","fraction",.5f,"GB");
    uint64_t scratch[akl::Capture<8,true>::words];
    recorder.Flush(capture.Data(),{scratch},0);
    assert(scratch[1]==2 && scratch[2]==3 && scratch[14]==9007199254740993ULL && scratch[15]==1);
    capture.Export(argv[1],0);
}

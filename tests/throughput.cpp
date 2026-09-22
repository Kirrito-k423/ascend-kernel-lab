// CPU contract only; no NPU timing claim.
#include "akl/kernel_latency.h"
#include <cassert>
using namespace akl::latency;
template<class F> void fails(F f) { bool caught=false;try{f();}catch(const std::exception&){caught=true;}assert(caught); }
int main() {
    {KernelLatencyTimer timer(0,2,nullptr,true);assert(!timer.Data() && !timer.WorkData());}
    const uint64_t epoch=1ULL<<60;
    std::array<std::array<uint64_t,4>,2> rows{{{epoch,epoch+500,0,1},{epoch+900000,epoch+901000,1,1}}};
    std::array<std::array<uint64_t,4>,2> work{{{1000000,400000,0,1},{2000000,600000,1,1}}};
    for (const char* mode:{"kernel","event"}) {
        setenv("AKL_LATENCY_MODE",mode,1);setenv("AKL_LATENCY_CLOCK_HZ","50000000",1);BeginKernelLatency(0,true);
        {KernelLatencyTimer timer(0,2,nullptr,true);timer.Start(nullptr);
         std::memcpy(timer.Data(),rows.data(),sizeof(rows));std::memcpy(timer.WorkData(),work.data(),sizeof(work));timer.Finish(nullptr);}
        uint64_t bytes[2]{};CopyWorkBytes(bytes,1);assert(bytes[0]==3000000 && bytes[1]==1000000);
        fails([&]{CopyWorkBytes(bytes,0);});float ms;EndLatency(&ms,1);
        assert(std::abs(ms-(std::string(mode)=="kernel"?.02f:.125f))<1e-7f);
    }
    for (bool overflow:{false,true}) {
        BeginKernelLatency(0,true);
        {KernelLatencyTimer timer(0,2,nullptr,true);timer.Start(nullptr);auto bad=work;
         if(overflow){bad[0][0]=UINT64_MAX;bad[1][0]=1;}else{bad[1][3]=0;}
         std::memcpy(timer.Data(),rows.data(),sizeof(rows));std::memcpy(timer.WorkData(),bad.data(),sizeof(bad));
         fails([&]{timer.Finish(nullptr);});}
        AbortLatency();
    }
    assert(allocations==freed);
}

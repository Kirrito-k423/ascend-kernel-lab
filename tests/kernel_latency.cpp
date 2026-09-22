// CPU 合约测试：不宣称真实 NPU 时间或屏障行为。
#include "akl/kernel_latency.h"
#include <cassert>
#include <iostream>
using namespace akl::latency;
template<class F> void fails(F f) {bool caught=false;try{f();}catch(const std::exception&){caught=true;}assert(caught);}
int main() {
    unsetenv("AKL_LATENCY_MODE");unsetenv("AKL_LATENCY_CLOCK_HZ");
    {KernelLatencyTimer t(0,2,nullptr);assert(!t.Data());t.Start(nullptr);t.Finish(nullptr);}
    assert(allocations==0);fails([]{BeginKernelLatency(0,true);});assert(!latency_session);
    for(const char* hz:{"nan","inf","0","-1","50oops",""}) {
        setenv("AKL_LATENCY_CLOCK_HZ",hz,1);fails([]{BeginKernelLatency(0,true);});assert(!latency_session);
    }
    // 巨大且不同的核内起点不能直接跨核相减，50 MHz 下 1000 tick = 20 us。
    const uint64_t epoch=1ULL<<60;
    std::vector<std::array<uint64_t,4>> rows{{epoch,epoch+500,0,1},{epoch+999999,epoch+1000999,1,1}};
    assert(std::abs(KernelMilliseconds(rows,50000000)-.02f)<1e-7f);
    assert(std::abs(KernelMilliseconds(rows,1000000000)-.001f)<1e-7f);
    auto bad=rows;bad[1][3]=0;fails([&]{KernelMilliseconds(bad,50000000);});
    bad=rows;bad[1][1]=0;fails([&]{KernelMilliseconds(bad,50000000);});
    bad=rows;bad[1][2]=0;fails([&]{KernelMilliseconds(bad,50000000);});
    fails([&]{KernelMilliseconds({},50000000);});
    setenv("AKL_LATENCY_CLOCK_HZ","50000000",1);BeginKernelLatency(0,true);
    fails([]{BeginKernelLatency(0,true);});fails([]{KernelLatencyTimer t(1,2,nullptr);});
    {KernelLatencyTimer t(0,2,nullptr);t.Start(nullptr);std::memcpy(t.Data(),rows.data(),64);t.Finish(nullptr);}
    float sample=0;EndLatency(&sample,1);assert(std::abs(sample-.02f)<1e-7f);assert(allocations==freed);
    BeginKernelLatency(0,false);
    {KernelLatencyTimer t(0,2,nullptr);t.Start(nullptr);fails([&]{t.Finish(nullptr);});}
    AbortLatency();assert(allocations==freed);
    BeginKernelLatency(0,true);
    {KernelLatencyTimer t(0,2,nullptr);t.Start(nullptr);fail_copy=1;fails([&]{t.Finish(nullptr);});fail_copy=0;}
    AbortLatency();assert(allocations==freed);
    setenv("AKL_LATENCY_MODE","event",1);unsetenv("AKL_LATENCY_CLOCK_HZ");BeginKernelLatency(0,true);
    {KernelLatencyTimer t(0,2,nullptr);assert(!t.Data());t.Start(nullptr);t.Finish(nullptr);}
    EndLatency(&sample,1);assert(sample==.125f);assert(allocations==freed);
    setenv("AKL_LATENCY_MODE","wrong",1);fails([]{BeginKernelLatency(0,true);});assert(!latency_session);
    std::cout<<"PASS: kernel 20 us vs event 125 us; uint64 deltas; missing/invalid configuration and data; inactive/event mode no allocation; cleanup\n";
}

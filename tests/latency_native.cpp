#include "akl/latency.h"
#include <cassert>
int main() {
    using namespace akl::latency;
    { LatencyTimer off(2);off.Start(nullptr);off.Finish(nullptr);assert(created==0); }
    BeginLatency(2,true);
    try {BeginLatency(2,true);assert(false);}catch(const std::runtime_error&){}
    try {LatencyTimer bad(3);assert(false);}catch(const std::runtime_error&){}
    for(int i=0;i<20;i++){LatencyTimer on(2);assert(on.SynchronizeStart());on.Start(nullptr);on.Finish(nullptr);}
    assert(created==2 && recorded==40 && synced==20 && LatencyCount()==20);
    float samples[20];EndLatency(samples,20);for(float f:samples)assert(f==.125f);
    assert(destroyed==2);AbortLatency();assert(destroyed==2);
    fail_create=created+2;
    try {BeginLatency(2,false);assert(false);}catch(const std::runtime_error&){}
    assert(destroyed==3 && !latency_session);
    fail_create=0;BeginLatency(2,false);fail_record=1;
    try {LatencyTimer timer(2);timer.Start(nullptr);assert(false);}catch(const std::runtime_error&){}
    AbortLatency();assert(destroyed==5);
}

#include "akl/kernel_latency.h"
#include "akl/kernel_latency_device.h"
#include <iostream>
using namespace AscendC;
__aicore__ inline void Spin(uint64_t ticks) {
    const uint64_t before=GetSystemCycle();
    while (uint64_t(GetSystemCycle())-before<ticks) {}
}
extern "C" __global__ __aicore__ void Probe(GM_ADDR output,uint64_t outside,uint64_t inside) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    Spin(outside); // 人为的区间外等待，非 SHMEM 屏障。
    const uint64_t start=GetSystemCycle();
    Spin(inside+100*GetBlockIdx());
    const uint64_t end=GetSystemCycle();
    Spin(outside);
    akl::latency::WriteKernelLatency(output,start,end);
}
int main(int argc, char** argv) {
    if (argc != 2) throw std::invalid_argument("usage: probe <idle-device-id>");
    const int device = std::stoi(argv[1]);
    using namespace akl::latency;
    CheckAcl(aclInit(nullptr),"init");CheckAcl(aclrtSetDevice(device),"device");
    aclrtStream stream=nullptr;CheckAcl(aclrtCreateStream(&stream),"stream");
    std::cout<<"mode,blocks,outside_ticks,iteration,elapsed_us\n";
    for(uint32_t blocks:{1u,8u}) for(uint64_t outside:{0ULL,50000ULL}) for(const char* mode:{"kernel","event"}) {
        setenv("AKL_LATENCY_MODE",mode,1);BeginKernelLatency(0,true);
        for(int i=0;i<8;++i) {
            KernelLatencyTimer timer(0,blocks,stream);timer.Start(stream);
            Probe<<<blocks,nullptr,stream>>>(timer.Data(),outside,5000);
            timer.Finish(stream);
        }
        float samples[8];EndLatency(samples,8);
        for(int i=0;i<8;++i)std::cout<<mode<<','<<blocks<<','<<outside<<','<<i<<','<<samples[i]*1000<<'\n';
    }
    CheckAcl(aclrtDestroyStream(stream),"destroy stream");CheckAcl(aclrtResetDevice(device),"reset");CheckAcl(aclFinalize(),"finalize");
}

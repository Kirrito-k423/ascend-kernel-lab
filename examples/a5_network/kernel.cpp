#include "kernel_operator.h"
#include "shmem.h"
using namespace AscendC;
extern "C" __global__ __aicore__ void network_kernel(GM_ADDR src,GM_ADDR dst,GM_ADDR records,
    uint32_t engine,uint32_t get,uint32_t partBytes,uint32_t slots,uint32_t operations,
    uint32_t batch,uint32_t qps,uint32_t control) {
    KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_AIV_ONLY);
    TPipe pipe; TBuf<TPosition::VECCALC> dataBuf,timeBuf;
    // 显式分配 scratch；不使用 SHMEM 隐含 UB 偏移。
    pipe.InitBuffer(dataBuf,32768);pipe.InitBuffer(timeBuf,32);
    auto data=dataBuf.Get<uint32_t>();auto times=timeBuf.Get<uint64_t>();
    Duplicate(data,uint32_t(0),8192);
    SetFlag<HardEvent::V_S>(EVENT_ID0);WaitFlag<HardEvent::V_S>(EVENT_ID0);
    PipeBarrier<PIPE_ALL>();
    const uint32_t core=GetBlockIdx();
    const uint64_t base=uint64_t(core)*partBytes*slots/4;
    auto x=reinterpret_cast<__gm__ uint32_t*>(src)+base;
    auto y=reinterpret_cast<__gm__ uint32_t*>(dst)+base;
    auto scratch=reinterpret_cast<__ubuf__ uint32_t*>(data.GetPhyAddr());
    uint32_t cursor=0;
    const uint64_t start=GetSystemCycle();
    for(uint32_t i=0;i<operations;++i) {
        const uint64_t offset=uint64_t(cursor)*partBytes/4;
        if(!control) {
            if(engine==0) {
                SetFlag<HardEvent::S_MTE2>(EVENT_ID0);WaitFlag<HardEvent::S_MTE2>(EVENT_ID0);
                if(get)aclshmemx_mte_get_nbi(y+offset,x+offset,scratch,32768,partBytes/4,1,0);
                else aclshmemx_mte_put_nbi(y+offset,x+offset,scratch,32768,partBytes/4,1,0);
                // 完成后才能复用单 UB scratch。
                SetFlag<HardEvent::MTE3_S>(EVENT_ID0);WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
            } else {
                const uint32_t qp=i%qps;
                if(get)aclshmemx_udma_qp_get_nbi(y+offset,x+offset,scratch,partBytes/4,1,qp,0);
                else aclshmemx_udma_qp_put_nbi(y+offset,x+offset,scratch,partBytes/4,1,qp,0);
                if((i+1)%batch==0)for(uint32_t q=0;q<qps;++q)aclshmemx_udma_qp_quiet(1,q);
            }
        } else asm volatile("" ::: "memory");
        if(++cursor==slots)cursor=0;
    }
    if(!control) {
        if(engine==0)aclshmemx_mte_quiet();
        else for(uint32_t q=0;q<qps;++q)aclshmemx_udma_qp_quiet(1,q);
    }
    PipeBarrier<PIPE_ALL>();
    const uint64_t end=GetSystemCycle();
    times.SetValue(0,start);times.SetValue(1,end);times.SetValue(2,core);
    times.SetValue(3,0x414b4c4e455431ULL);
    GlobalTensor<uint64_t> out;out.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(records));
    SetFlag<HardEvent::S_MTE3>(EVENT_ID0);WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
    DataCopy(out[core*4],times,4);
    SetFlag<HardEvent::MTE3_S>(EVENT_ID0);WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
}
extern "C" void launch_network(void* stream,void* src,void* dst,void* records,
    uint32_t cores,uint32_t engine,uint32_t get,uint32_t partBytes,uint32_t slots,
    uint32_t operations,uint32_t batch,uint32_t qps,uint32_t control) {
    network_kernel<<<cores,nullptr,stream>>>(static_cast<uint8_t*>(src),static_cast<uint8_t*>(dst),
        static_cast<uint8_t*>(records),engine,get,partBytes,slots,operations,batch,qps,control);
}

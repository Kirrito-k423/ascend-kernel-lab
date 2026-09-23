#pragma once
#include "kernel_operator.h"
#define AKL_SHAPE_INLINE __aicore__ inline
#include "akl/datacopy_shape_protocol.h"
#undef AKL_SHAPE_INLINE
namespace akl::copy_shape {
template<typename T> __aicore__ constexpr uint32_t Dtype() { return 0; }
template<> __aicore__ constexpr uint32_t Dtype<half>() { return 1; }
template<> __aicore__ constexpr uint32_t Dtype<bfloat16_t>() { return 2; }
template<> __aicore__ constexpr uint32_t Dtype<float>() { return 3; }
template<> __aicore__ constexpr uint32_t Dtype<int32_t>() { return 4; }
template<> __aicore__ constexpr uint32_t Dtype<uint32_t>() { return 5; }
template<> __aicore__ constexpr uint32_t Dtype<uint8_t>() { return 6; }
class Recorder {
public:
    template<typename T>
    __aicore__ inline void Observe(uint32_t slot, uint32_t site, uint32_t scene, uint32_t sequence,
                                   uint32_t api, bool store, uint32_t blocks, uint32_t length,
                                   uint32_t srcStride, uint32_t dstStride,
                                   AscendC::GlobalTensor<T> gm, AscendC::LocalTensor<T> ub) {
        const uint64_t descriptor[11] = {site, scene, sequence, api, uint32_t(store), Dtype<T>(),
            sizeof(T), blocks, length, srcStride, dstStride};
        table_.Observe(slot, descriptor, reinterpret_cast<uint64_t>(gm.GetPhyAddr()),
                       reinterpret_cast<uint64_t>(ub.GetPhyAddr()));
    }
    // 只能在业务流水完成后调用；不在 Observe 中增加屏障、GM写或打印。
    __aicore__ inline void Flush(GM_ADDR output, AscendC::LocalTensor<uint64_t> scratch) {
        using namespace AscendC;
        for (uint32_t i = 0; i < kWords; ++i) scratch.SetValue(i, 0);
        scratch.SetValue(0, kMagic); scratch.SetValue(1, 1);
        scratch.SetValue(2, kSlots); scratch.SetValue(3, table_.dropped);
        scratch.SetValue(4, GetBlockIdx()); scratch.SetValue(5, GetSubBlockIdx());
        scratch.SetValue(6, kRecordWords); scratch.SetValue(7, 1);
        for (uint32_t s = 0; s < kSlots; ++s)
            for (uint32_t w = 0; w < kRecordWords; ++w)
                scratch.SetValue(8+s*kRecordWords+w, table_.rows[s][w]);
        SetFlag<HardEvent::S_MTE3>(EVENT_ID0); WaitFlag<HardEvent::S_MTE3>(EVENT_ID0);
        GlobalTensor<uint64_t> dst;
        dst.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(output)+uint64_t(GetBlockIdx())*kWords);
        DataCopy(dst, scratch, kWords);
        SetFlag<HardEvent::MTE3_S>(EVENT_ID0); WaitFlag<HardEvent::MTE3_S>(EVENT_ID0);
    }
private:
    Table table_;
};
}

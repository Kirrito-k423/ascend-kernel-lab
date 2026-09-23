#pragma once
#include "kernel_operator.h"
#include "akl/params.h"

namespace akl {
// 每 AIV 独立、固定容量；打点不插入 barrier。只有 flush 使用调用方专属 UB。
template<bool Enabled, uint32_t Capacity = kCapacity>
class Recorder {
public:
    __aicore__ inline uint32_t Written() const { return count_; }
    static_assert(Capacity > 0 && Capacity % 2 == 0, "容量须为正偶数以保证32B对齐");
    __aicore__ inline void Mark(uint32_t id) {
        if constexpr (Enabled) At(id, AscendC::GetSystemCycle());
    }
    __aicore__ inline void At(uint32_t id, uint64_t tick) {
        if constexpr (Enabled) {
            if (count_ < Capacity) { ticks_[count_] = tick; ids_[count_++] = id; }
            else ++dropped_;
        }
    }
    __aicore__ inline void Flush(GM_ADDR output, AscendC::LocalTensor<uint64_t> scratch,
                                 uint64_t retained) {
        if constexpr (Enabled) {
            constexpr uint32_t words = 8 + 2 * Capacity;
            static_assert(words % 4 == 0, "记录区必须32字节对齐");
            for (uint32_t i = 0; i < words; ++i) scratch.SetValue(i, 0);
            scratch.SetValue(0, kMagic);
            scratch.SetValue(1, 1);
            scratch.SetValue(2, count_);
            scratch.SetValue(3, dropped_);
            scratch.SetValue(4, AscendC::GetBlockIdx());
            scratch.SetValue(5, AscendC::GetSubBlockIdx());
            scratch.SetValue(6, retained);
            scratch.SetValue(7, 1);
            for (uint32_t i = 0; i < count_; ++i) {
                scratch.SetValue(8 + 2 * i, ids_[i]);
                scratch.SetValue(9 + 2 * i, ticks_[i]);
            }
            AscendC::SetFlag<AscendC::HardEvent::S_MTE3>(EVENT_ID0);
            AscendC::WaitFlag<AscendC::HardEvent::S_MTE3>(EVENT_ID0);
            AscendC::GlobalTensor<uint64_t> dst;
            dst.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t*>(output));
            AscendC::DataCopy(dst[AscendC::GetBlockIdx() * words], scratch, words);
            AscendC::SetFlag<AscendC::HardEvent::MTE3_S>(EVENT_ID0);
            AscendC::WaitFlag<AscendC::HardEvent::MTE3_S>(EVENT_ID0);
        }
    }
private:
    uint64_t ticks_[Enabled ? Capacity : 1];
    uint32_t ids_[Enabled ? Capacity : 1];
    uint32_t count_ = 0;
    uint64_t dropped_ = 0;
};
}

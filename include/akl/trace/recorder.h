#pragma once
#include "kernel_operator.h"
#include "akl/params.h"
#include <type_traits>

namespace akl {
// 每 AIV 独立、固定容量；打点不插入 barrier。只有 flush 使用调用方专属 UB。
template<bool Enabled, uint32_t Capacity = kCapacity, bool Quantities = false>
class Recorder {
public:
    static_assert(Capacity > 0 && Capacity % 2 == 0, "容量须为正偶数以保证32B对齐");
    __aicore__ inline void Mark(uint32_t id) {
        if constexpr (Enabled) At(id, AscendC::GetSystemCycle());
    }
    template<typename Number>
    __aicore__ inline void Work(uint32_t id, Number amount) {
        static_assert(Quantities, "处理量打点需要 Recorder 的 Quantities=true");
        if constexpr (Enabled) {
            uint32_t slot = count_;
            At(id, AscendC::GetSystemCycle());
            if (slot < Capacity) {
                // 整数原样保留 uint64；小数保留 float32 位模式，不在设备计算速度。
                union { float value; uint32_t bits; } packed{static_cast<float>(amount)};
                amounts_[slot] = std::is_integral_v<Number> ? uint64_t(amount) : packed.bits;
                kinds_[slot] = amount < 0 ? 3 : (std::is_integral_v<Number> ? 1 : 2);
            }
        }
    }
    __aicore__ inline void At(uint32_t id, uint64_t tick) {
        if constexpr (Enabled) {
            if (count_ < Capacity) {
                if constexpr (Quantities) kinds_[count_] = 0;
                ticks_[count_] = tick; ids_[count_++] = id;
            }
            else ++dropped_;
        }
    }
    __aicore__ inline void Flush(GM_ADDR output, AscendC::LocalTensor<uint64_t> scratch,
                                 uint64_t retained) {
        if constexpr (Enabled) {
            constexpr uint32_t stride = Quantities ? 4 : 2, words = 8 + stride * Capacity;
            static_assert(words % 4 == 0, "记录区必须32字节对齐");
            for (uint32_t i = 0; i < words; ++i) scratch.SetValue(i, 0);
            scratch.SetValue(0, kMagic);
            scratch.SetValue(1, Quantities ? 2 : 1);
            scratch.SetValue(2, count_);
            scratch.SetValue(3, dropped_);
            scratch.SetValue(4, AscendC::GetBlockIdx());
            scratch.SetValue(5, AscendC::GetSubBlockIdx());
            scratch.SetValue(6, retained);
            scratch.SetValue(7, 1);
            for (uint32_t i = 0; i < count_; ++i) {
                scratch.SetValue(8 + stride * i, ids_[i]);
                scratch.SetValue(9 + stride * i, ticks_[i]);
                if constexpr (Quantities) {
                    scratch.SetValue(10 + stride * i, kinds_[i] ? amounts_[i] : 0);
                    scratch.SetValue(11 + stride * i, kinds_[i]);
                }
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
    uint64_t amounts_[Enabled && Quantities ? Capacity : 1];
    uint32_t kinds_[Enabled && Quantities ? Capacity : 1];
    uint32_t count_ = 0;
    uint64_t dropped_ = 0;
};
}

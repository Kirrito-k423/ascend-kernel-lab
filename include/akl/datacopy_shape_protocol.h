#pragma once
#include <cstdint>
#ifndef AKL_SHAPE_INLINE
#define AKL_SHAPE_INLINE inline
#endif
namespace akl::copy_shape {
constexpr uint64_t kMagic = 0x414b4c4350593031ULL;
constexpr uint32_t kSlots = 24;
constexpr uint32_t kRecordWords = 20;
constexpr uint32_t kWords = 8 + kSlots * kRecordWords;
// 描述符: site, scene, anchor_sequence, api, direction, dtype, element_bytes,
// block_count, raw_length, raw_src_stride, raw_dst_stride。
// API: 0=DataCopy(count), 1=DataCopy(params), 2=DataCopyPad(params)。
// dtype: 0=unknown, 1=float16, 2=bfloat16, 3=float32, 4=int32, 5=uint32, 6=uint8。
struct Table {
    uint64_t rows[kSlots][kRecordWords]{};
    uint64_t dropped = 0;
    AKL_SHAPE_INLINE void Observe(uint32_t slot, const uint64_t* descriptor,
                                 uint64_t gmAddress, uint64_t ubAddress) {
        if (slot >= kSlots) { ++dropped; return; }
        auto* row = rows[slot];
        if (!row[11]) {
            for (uint32_t i = 0; i < 11; ++i) row[i] = descriptor[i];
        } else {
            bool changed = false;
            for (uint32_t i = 0; i < 11; ++i) if (row[i] != descriptor[i]) changed = true;
            if (changed) ++row[12];
        }
        ++row[11]; // 调用次数；形状变化会显式失效，不能静默套用首次形状。
        // 位集合仅保留对齐分布：bit i表示mod32=i，512B范围另按32B分桶；不导出地址。
        row[13] |= uint64_t(1) << (gmAddress % 32);
        row[14] |= uint64_t(1) << ((gmAddress % 512) / 32);
        row[15] |= uint64_t(1) << (ubAddress % 32);
        row[16] = row[3] == 0 ? row[8] * row[6] : row[7] * row[8] * (row[3] == 1 ? 32 : 1);
    }
};
}

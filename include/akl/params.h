#pragma once
#include <cstdint>
namespace akl {
// ABI 1：与 Python FIELD_NAMES 顺序一致；启动前检查 sizeof。
struct Params {
    uint32_t op, elements, repeats, pattern, reduce, loops;
    uint32_t payload_bytes, block_count, gap_bytes, delay_ticks;
    uint32_t input_stride, output_stride;
    uint32_t src0_block_stride, src0_repeat_stride, src1_repeat_stride;
};
constexpr uint64_t kMagic = 0x414b4c5452433031ULL;
constexpr uint32_t kCapacity = 16;
constexpr uint32_t kWords = 8 + 2 * kCapacity;
}

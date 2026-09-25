#pragma once
#include <cstdint>
namespace akl {
// 独立 DataCopy ABI，不能与旧 micro Params 混用。
struct CopyParams {
    uint32_t direction, api, element_bytes, block_bytes, blocks, gm_gap_bytes;
    uint32_t gm_offset_bytes, ub_offset_bytes, loops, batch, control, slots;
    // reserved1: 0=每批完成，1=双窗口；保留64字节ABI及旧模式编码。
    uint32_t gm_stride_bytes, ub_stride_bytes, reserved0, reserved1;
};
}

#pragma once
#include "akl/params.h"
#include <acl/acl.h>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <memory>
#include <cstring>
#include <unistd.h>

namespace akl {
// 每次 launch 独占 GM，析构前等待所属流，不能挪用业务 workspace。
template<uint32_t Capacity = kCapacity>
class Capture {
public:
    static constexpr uint32_t words = 8 + 2 * Capacity;
    Capture(uint32_t blocks, void* stream) : blocks_(blocks), stream_(stream) {
        static_assert(Capacity > 0 && Capacity % 2 == 0, "容量须为正偶数以保证32B对齐");
        if (!blocks || blocks > std::numeric_limits<size_t>::max() / (words * sizeof(uint64_t)))
            throw std::invalid_argument("trace block count overflow");
        bytes_ = size_t(blocks) * words * sizeof(uint64_t);
        Check(aclrtGetDevice(&device_));
        Check(aclrtMalloc(&data_, bytes_, ACL_MEM_MALLOC_HUGE_FIRST));
        auto status = aclrtMemset(data_, bytes_, 0, bytes_);
        if (status != ACL_SUCCESS) { aclrtFree(data_); data_ = nullptr; Check(status); }
    }
    Capture(const Capture&) = delete;
    Capture& operator=(const Capture&) = delete;
    ~Capture() {
        // 同步失败时宁可保留分配，也不释放可能仍被 kernel 写入的地址。
        if (data_ && aclrtSynchronizeStream(stream_) == ACL_SUCCESS) aclrtFree(data_);
    }
    uint8_t* Data() const { return static_cast<uint8_t*>(data_); }
    void Export(const std::filesystem::path& root, uint32_t rank) {
        Check(aclrtSynchronizeStream(stream_));
        // ACL 页锁定内存避免普通 vector 的 pageable D2H 暂存拷贝；异常路径也释放。
        void* host = nullptr;
        Check(aclrtMallocHost(&host, bytes_));
        std::unique_ptr<void, decltype(&aclrtFreeHost)> owner(host, aclrtFreeHost);
        auto raw = static_cast<uint64_t*>(host);
        Check(aclrtMemcpy(raw, bytes_, data_, bytes_, ACL_MEMCPY_DEVICE_TO_HOST));
        // ABI 仍为定长行，仅缩短所有 block 都未使用的尾部槽位。
        // 缺失/损坏行保留完整原始 buffer，交给离线校验报告，不能掩盖采集失败。
        uint32_t kept = 0;
        for (uint32_t b = 0; b < blocks_; ++b) {
            const auto* row = raw + size_t(b) * words;
            if (row[0] != kMagic || row[1] != 1 || row[7] != 1 || row[4] != b || row[2] > Capacity) {
                kept = Capacity;
                break;
            }
            if (row[2] > kept) kept = static_cast<uint32_t>(row[2]);
        }
        // 向上取偶数（21→22），至少 2 个槽，保持正偶数容量和 32B 行对齐。
        const uint32_t stored = kept ? (kept + 1) & ~1u : 2;
        const size_t storedWords = 8 + 2 * stored;
        // 顺序向前紧凑排列，源/目标可能重叠，必须用 memmove。
        if (stored < Capacity) for (uint32_t b = 1; b < blocks_; ++b)
            std::memmove(raw + size_t(b) * storedWords, raw + size_t(b) * words, storedWords * 8);
        static std::atomic<uint64_t> sequence{0};
        std::filesystem::create_directories(root);
        std::filesystem::path folder;
        do {
            folder = root / ("rank" + std::to_string(rank) + "-pid" + std::to_string(getpid()) +
                "-launch" + std::to_string(sequence.fetch_add(1)));
        } while (!std::filesystem::create_directory(folder));
        Write(folder / "trace.bin", reinterpret_cast<const char*>(raw), size_t(blocks_) * storedWords * 8);
        const auto metadata = "{\"schema\":\"akl.semantic.v1\",\"capacity\":" + std::to_string(stored) + ",\"recorder_capacity\":" + std::to_string(Capacity) +
            ",\"blocks\":" + std::to_string(blocks_) + ",\"rank\":" + std::to_string(rank) +
            ",\"device\":" + std::to_string(device_) + ",\"alignment\":\"unverified\"}";
        Write(folder / "capture.json", metadata.data(), metadata.size());
    }
private:
    static void Check(aclError status) {
        if (status != ACL_SUCCESS) throw std::runtime_error("trace ACL status=" + std::to_string(status));
    }
    static void Write(const std::filesystem::path& path, const char* data, size_t bytes) {
        std::ofstream out;
        out.exceptions(std::ios::failbit | std::ios::badbit);
        out.open(path, std::ios::binary);
        out.write(data, bytes);
        out.close();
    }
    uint32_t blocks_;
    int32_t device_ = 0;
    void* stream_;
    void* data_ = nullptr;
    size_t bytes_ = 0;
};
}

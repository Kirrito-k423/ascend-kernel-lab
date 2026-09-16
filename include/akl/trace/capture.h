#pragma once
#include "akl/params.h"
#include <acl/acl.h>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
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
        std::vector<uint64_t> raw(bytes_ / sizeof(uint64_t));
        Check(aclrtMemcpy(raw.data(), bytes_, data_, bytes_, ACL_MEMCPY_DEVICE_TO_HOST));
        static std::atomic<uint64_t> sequence{0};
        std::filesystem::create_directories(root);
        std::filesystem::path folder;
        do {
            folder = root / ("rank" + std::to_string(rank) + "-pid" + std::to_string(getpid()) +
                "-launch" + std::to_string(sequence.fetch_add(1)));
        } while (!std::filesystem::create_directory(folder));
        Write(folder / "trace.bin", reinterpret_cast<const char*>(raw.data()), bytes_);
        const auto metadata = "{\"schema\":\"akl.semantic.v1\",\"capacity\":" + std::to_string(Capacity) +
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

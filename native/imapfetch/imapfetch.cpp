// imapfetch：新邮件下载引擎（Python 扩展，pybind11 + OpenSSL + zlib）。
//
// 每个账号 + 文件夹一个 Engine：常驻一条已登录、已 SELECT、（支持时）已开 COMPRESS=DEFLATE 的主连接，
// 收到新邮件推送后：
//   1. 主连接 UID FETCH last+1:* 取头部（UID、FLAGS、INTERNALDATE、RFC822.SIZE、X-GM-THRID、
//      X-GM-LABELS、BODY.PEEK[HEADER]），立刻回调 Python（on_headers）；
//   2. 正文：小邮件在主连接一条命令取齐，大邮件分给按需打开的副连接并发取；每取到一封回调
//      一次（on_body(uid, bytes)）。网络等待期间释放 GIL，回调时重新获取。
//
// 只实现引擎需要的 IMAP 子集：CAPABILITY、LOGIN / AUTHENTICATE XOAUTH2、SELECT、COMPRESS、
// UID FETCH、NOOP、LOGOUT；响应解析支持 literal、带引号串、括号列表。凭据与令牌由 Python 提供。
// 任何错误都转成 RuntimeError 抛回 Python，Python 侧会丢弃引擎并退回自己的快速路径。
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <openssl/err.h>
#include <openssl/ssl.h>
#include <zlib.h>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

// ---- 工具 ----------------------------------------------------------------------

std::string upper(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

bool starts_with(const std::string& s, const char* prefix) {
    size_t n = std::strlen(prefix);
    return s.size() >= n && s.compare(0, n, prefix) == 0;
}

bool starts_with(const std::string& s, const std::string& prefix) {
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

std::string base64_encode(const std::string& in) {
    static const char* alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    size_t i = 0;
    while (i + 2 < in.size()) {
        uint32_t v = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8) | static_cast<unsigned char>(in[i + 2]);
        out.push_back(alpha[(v >> 18) & 63]);
        out.push_back(alpha[(v >> 12) & 63]);
        out.push_back(alpha[(v >> 6) & 63]);
        out.push_back(alpha[v & 63]);
        i += 3;
    }
    if (i + 1 == in.size()) {
        uint32_t v = static_cast<unsigned char>(in[i]) << 16;
        out.push_back(alpha[(v >> 18) & 63]);
        out.push_back(alpha[(v >> 12) & 63]);
        out += "==";
    } else if (i + 2 == in.size()) {
        uint32_t v = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8);
        out.push_back(alpha[(v >> 18) & 63]);
        out.push_back(alpha[(v >> 12) & 63]);
        out.push_back(alpha[(v >> 6) & 63]);
        out += "=";
    }
    return out;
}

// IMAP 带引号串：反斜杠转义引号和反斜杠
std::string quote(const std::string& s) {
    std::string out = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\') out.push_back('\\');
        out.push_back(c);
    }
    out.push_back('"');
    return out;
}

std::string ssl_error_text() {
    unsigned long code = ERR_get_error();
    if (!code) return "TLS error";
    char buf[256];
    ERR_error_string_n(code, buf, sizeof buf);
    ERR_clear_error();
    return buf;
}

// ---- 传输层：TCP / TLS / DEFLATE ----------------------------------------------------

class Transport {
public:
    Transport() = default;
    ~Transport() { close(); }
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    void connect(const std::string& host, int port, bool tls, int timeout_sec) {
        struct addrinfo hints{};
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;
        struct addrinfo* res = nullptr;
        std::string port_text = std::to_string(port);
        int rc = getaddrinfo(host.c_str(), port_text.c_str(), &hints, &res);
        if (rc != 0) throw std::runtime_error(std::string("DNS: ") + gai_strerror(rc));
        int sock = -1;
        std::string last_error = "connect failed";
        for (struct addrinfo* ai = res; ai != nullptr; ai = ai->ai_next) {
            sock = ::socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
            if (sock < 0) continue;
            struct timeval tv{};
            tv.tv_sec = timeout_sec;
            setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
            setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
            if (::connect(sock, ai->ai_addr, ai->ai_addrlen) == 0) break;
            last_error = std::string("connect: ") + std::strerror(errno);
            ::close(sock);
            sock = -1;
        }
        freeaddrinfo(res);
        if (sock < 0) throw std::runtime_error(last_error);
        int one = 1;
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof one);
        fd_ = sock;
        if (tls) {
            ctx_ = SSL_CTX_new(TLS_client_method());
            if (!ctx_) throw std::runtime_error(ssl_error_text());
            SSL_CTX_set_min_proto_version(ctx_, TLS1_2_VERSION);
            SSL_CTX_set_default_verify_paths(ctx_);
            SSL_CTX_set_verify(ctx_, SSL_VERIFY_PEER, nullptr);
            ssl_ = SSL_new(ctx_);
            if (!ssl_) throw std::runtime_error(ssl_error_text());
            SSL_set_tlsext_host_name(ssl_, host.c_str());
            SSL_set1_host(ssl_, host.c_str());
            SSL_set_fd(ssl_, fd_);
            if (SSL_connect(ssl_) != 1) throw std::runtime_error("TLS handshake: " + ssl_error_text());
        }
    }

    void close() {
        if (ssl_) {
            SSL_shutdown(ssl_);
            SSL_free(ssl_);
            ssl_ = nullptr;
        }
        if (ctx_) {
            SSL_CTX_free(ctx_);
            ctx_ = nullptr;
        }
        if (fd_ >= 0) {
            ::close(fd_);
            fd_ = -1;
        }
        if (compressed_) {
            inflateEnd(&in_);
            deflateEnd(&out_);
            compressed_ = false;
        }
    }

    bool open() const { return fd_ >= 0; }

    void enable_compression() {
        std::memset(&in_, 0, sizeof in_);
        std::memset(&out_, 0, sizeof out_);
        if (inflateInit2(&in_, -15) != Z_OK) throw std::runtime_error("inflateInit failed");
        if (deflateInit2(&out_, 6, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
            inflateEnd(&in_);
            throw std::runtime_error("deflateInit failed");
        }
        // 缓冲区里还没消费的字节已经是压缩数据：转入待解压队列
        compressed_ = true;
        if (!buf_.empty()) {
            std::string pending;
            pending.swap(buf_);
            inflate_into(pending);
        }
    }

    bool compressed() const { return compressed_; }

    void write(const std::string& data) {
        if (compressed_) {
            std::string packed;
            out_.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(data.data()));
            out_.avail_in = static_cast<uInt>(data.size());
            char chunk[16384];
            do {
                out_.next_out = reinterpret_cast<Bytef*>(chunk);
                out_.avail_out = sizeof chunk;
                int rc = deflate(&out_, Z_SYNC_FLUSH);
                if (rc == Z_STREAM_ERROR) throw std::runtime_error("deflate failed");
                packed.append(chunk, sizeof chunk - out_.avail_out);
            } while (out_.avail_out == 0);
            raw_write(packed);
        } else {
            raw_write(data);
        }
    }

    // 读一行（不含 CRLF）。连接关闭时抛异常。
    std::string read_line() {
        while (true) {
            size_t pos = buf_.find('\n');
            if (pos != std::string::npos) {
                std::string line = buf_.substr(0, pos);
                buf_.erase(0, pos + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                return line;
            }
            fill();
        }
    }

    std::string read_exact(size_t n) {
        while (buf_.size() < n) fill();
        std::string out = buf_.substr(0, n);
        buf_.erase(0, n);
        return out;
    }

private:
    void raw_write(const std::string& data) {
        size_t sent = 0;
        while (sent < data.size()) {
            ssize_t n;
            if (ssl_) {
                int r = SSL_write(ssl_, data.data() + sent, static_cast<int>(data.size() - sent));
                n = r;
                if (r <= 0) throw std::runtime_error("TLS write: " + ssl_error_text());
            } else {
                n = ::send(fd_, data.data() + sent, data.size() - sent, MSG_NOSIGNAL);
                if (n <= 0) throw std::runtime_error(std::string("send: ") + std::strerror(errno));
            }
            sent += static_cast<size_t>(n);
        }
    }

    void fill() {
        char chunk[65536];
        ssize_t n;
        if (ssl_) {
            int r = SSL_read(ssl_, chunk, sizeof chunk);
            if (r <= 0) {
                int err = SSL_get_error(ssl_, r);
                if (err == SSL_ERROR_ZERO_RETURN) throw std::runtime_error("connection closed");
                throw std::runtime_error("TLS read: " + ssl_error_text() + " (" + std::strerror(errno) + ")");
            }
            n = r;
        } else {
            n = ::recv(fd_, chunk, sizeof chunk, 0);
            if (n == 0) throw std::runtime_error("connection closed");
            if (n < 0) throw std::runtime_error(std::string("recv: ") + std::strerror(errno));
        }
        std::string data(chunk, static_cast<size_t>(n));
        if (compressed_) inflate_into(data);
        else buf_ += data;
    }

    void inflate_into(const std::string& data) {
        in_.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(data.data()));
        in_.avail_in = static_cast<uInt>(data.size());
        char chunk[65536];
        do {
            in_.next_out = reinterpret_cast<Bytef*>(chunk);
            in_.avail_out = sizeof chunk;
            int rc = inflate(&in_, Z_SYNC_FLUSH);
            if (rc != Z_OK && rc != Z_BUF_ERROR && rc != Z_STREAM_END) throw std::runtime_error("inflate failed");
            buf_.append(chunk, sizeof chunk - in_.avail_out);
            if (rc == Z_STREAM_END) break;
        } while (in_.avail_out == 0 || in_.avail_in > 0);
    }

    int fd_ = -1;
    SSL_CTX* ctx_ = nullptr;
    SSL* ssl_ = nullptr;
    bool compressed_ = false;
    z_stream in_{};
    z_stream out_{};
    std::string buf_;
};

// ---- IMAP 响应解析 ---------------------------------------------------------------

struct Node {
    enum Kind { ATOM, STR, LIST, NIL } kind = ATOM;
    std::string text;          // ATOM / STR
    std::vector<Node> items;   // LIST
};

// 一条逻辑响应 = 文本段与 literal 交替
struct Segment {
    std::string text;
    bool literal = false;
};

class Tokenizer {
public:
    explicit Tokenizer(const std::vector<Segment>& segs) : segs_(segs) {}

    std::vector<Node> parse_all() {
        std::vector<Node> out;
        while (true) {
            Node n;
            if (!next(n)) break;
            out.push_back(std::move(n));
        }
        return out;
    }

private:
    bool next(Node& out) {
        while (true) {
            if (seg_ >= segs_.size()) return false;
            const Segment& seg = segs_[seg_];
            if (seg.literal) {
                out.kind = Node::STR;
                out.text = seg.text;
                ++seg_;
                pos_ = 0;
                return true;
            }
            if (pos_ >= seg.text.size()) {
                ++seg_;
                pos_ = 0;
                continue;
            }
            char c = seg.text[pos_];
            if (c == ' ') { ++pos_; continue; }
            if (c == ')') { ++pos_; return false; }   // 列表结束，由调用方处理
            if (c == '(') {
                ++pos_;
                out.kind = Node::LIST;
                out.items.clear();
                while (true) {
                    Node item;
                    if (!next_in_list(item)) break;
                    out.items.push_back(std::move(item));
                }
                return true;
            }
            if (c == '"') {
                ++pos_;
                out.kind = Node::STR;
                out.text.clear();
                while (pos_ < seg.text.size() && seg.text[pos_] != '"') {
                    if (seg.text[pos_] == '\\' && pos_ + 1 < seg.text.size()) ++pos_;
                    out.text.push_back(seg.text[pos_]);
                    ++pos_;
                }
                if (pos_ < seg.text.size()) ++pos_;   // 结束引号
                return true;
            }
            // atom：到空格或括号为止；BODY[HEADER] 这类含方括号的原子整体保留
            size_t start = pos_;
            int bracket = 0;
            while (pos_ < seg.text.size()) {
                char d = seg.text[pos_];
                if (d == '[') ++bracket;
                else if (d == ']') { if (bracket > 0) --bracket; }
                else if ((d == ' ' || d == '(' || d == ')') && bracket == 0) break;
                ++pos_;
            }
            out.text = seg.text.substr(start, pos_ - start);
            out.kind = (upper(out.text) == "NIL") ? Node::NIL : Node::ATOM;
            return true;
        }
    }

    bool next_in_list(Node& out) {
        // 与 next 相同，但遇到 ')' 返回 false 表示列表结束
        while (true) {
            if (seg_ >= segs_.size()) return false;
            const Segment& seg = segs_[seg_];
            if (seg.literal) return next(out);
            if (pos_ >= seg.text.size()) { ++seg_; pos_ = 0; continue; }
            char c = seg.text[pos_];
            if (c == ' ') { ++pos_; continue; }
            if (c == ')') { ++pos_; return false; }
            return next(out);
        }
    }

    const std::vector<Segment>& segs_;
    size_t seg_ = 0;
    size_t pos_ = 0;
};

uint64_t to_number(const Node& n) {
    if (n.kind != Node::ATOM || n.text.empty()) return 0;
    uint64_t v = 0;
    for (char c : n.text) {
        if (c < '0' || c > '9') return 0;
        v = v * 10 + static_cast<uint64_t>(c - '0');
    }
    return v;
}

struct FetchRecord {
    uint64_t uid = 0;
    std::vector<std::string> flags;
    std::string internaldate;
    uint64_t size = 0;
    bool has_thrid = false;
    uint64_t thrid = 0;
    std::vector<std::string> labels;
    bool has_header = false;
    std::string header;
    bool has_body = false;
    std::string body;
};

// 解析 "* n FETCH (...)" 的括号列表
FetchRecord parse_fetch(const std::vector<Segment>& segs) {
    Tokenizer tk(segs);
    std::vector<Node> nodes = tk.parse_all();
    FetchRecord rec;
    // nodes: "*", seq, "FETCH", LIST
    const Node* list = nullptr;
    for (const auto& n : nodes) if (n.kind == Node::LIST) { list = &n; break; }
    if (!list) return rec;
    const auto& items = list->items;
    for (size_t i = 0; i + 1 < items.size(); i += 2) {
        std::string key = upper(items[i].text);
        const Node& val = items[i + 1];
        if (key == "UID") rec.uid = to_number(val);
        else if (key == "FLAGS") { for (const auto& f : val.items) rec.flags.push_back(f.text); }
        else if (key == "INTERNALDATE") rec.internaldate = val.text;
        else if (key == "RFC822.SIZE") rec.size = to_number(val);
        else if (key == "X-GM-THRID") { rec.has_thrid = true; rec.thrid = to_number(val); }
        else if (key == "X-GM-LABELS") { for (const auto& l : val.items) rec.labels.push_back(l.text); }
        else if (key == "BODY[HEADER]") { rec.has_header = true; rec.header = (val.kind == Node::NIL) ? "" : val.text; }
        else if (key == "BODY[]") { rec.has_body = true; rec.body = (val.kind == Node::NIL) ? "" : val.text; }
    }
    return rec;
}

// ---- IMAP 连接 -------------------------------------------------------------------

struct Config {
    std::string host;
    int port = 993;
    std::string user;
    std::string password;
    std::string token;
    std::string folder;
    int extra_connections = 2;
    size_t small_body_bytes = 256 * 1024;
    int idle_close_sec = 600;
    bool compress = true;
    int timeout_sec = 60;
    bool plain_tcp = false;
};

struct Tagged {
    std::string status;   // OK / NO / BAD
    std::string text;
};

class Connection {
public:
    explicit Connection(const Config& cfg) : cfg_(cfg) {}

    void open() {
        t_.connect(cfg_.host, cfg_.port, !cfg_.plain_tcp, cfg_.timeout_sec);
        std::string greeting = t_.read_line();
        if (!starts_with(greeting, "* OK") && !starts_with(greeting, "* PREAUTH")) {
            throw std::runtime_error("unexpected greeting: " + greeting);
        }
        capability();
        if (!cfg_.token.empty()) {
            std::string payload = "user=" + cfg_.user + "\x01" + "auth=Bearer " + cfg_.token + "\x01\x01";
            Tagged r = command("AUTHENTICATE XOAUTH2 " + base64_encode(payload), nullptr, true);
            if (r.status != "OK") throw std::runtime_error("AUTHENTICATE failed: " + r.text);
        } else {
            Tagged r = command("LOGIN " + quote(cfg_.user) + " " + quote(cfg_.password), nullptr, false);
            if (r.status != "OK") throw std::runtime_error("LOGIN failed: " + r.text);
        }
        capability();   // 登录后能力可能变化（Gmail 在登录后才宣告 X-GM-EXT-1）
        if (cfg_.compress && has_capability("COMPRESS=DEFLATE")) {
            Tagged r = command("COMPRESS DEFLATE", nullptr, false);
            if (r.status == "OK") t_.enable_compression();
        }
        Tagged r = command("SELECT " + quote(cfg_.folder), nullptr, false);
        if (r.status != "OK") throw std::runtime_error("SELECT failed: " + r.text);
        last_used_ = std::chrono::steady_clock::now();
    }

    void close() {
        if (t_.open()) {
            try { command("LOGOUT", nullptr, false); } catch (...) {}
        }
        t_.close();
    }

    bool alive() const { return t_.open() && !dead_; }
    bool compressed() const { return t_.compressed(); }
    bool has_capability(const std::string& cap) const { return caps_.find(" " + upper(cap) + " ") != std::string::npos; }
    std::chrono::steady_clock::time_point last_used() const { return last_used_; }

    // 执行一条命令；每条 untagged 响应（含 literal）交给 handler。连接错误后标记 dead。
    Tagged command(const std::string& cmd, const std::function<void(const std::vector<Segment>&)>& handler, bool authenticate) {
        if (!t_.open()) throw std::runtime_error("not connected");
        std::string tag = "A" + std::to_string(++tag_counter_);
        try {
            t_.write(tag + " " + cmd + "\r\n");
            while (true) {
                std::vector<Segment> segs = read_response();
                const std::string& first = segs.front().text;
                if (starts_with(first, tag + " ")) {
                    Tagged r;
                    size_t sp = first.find(' ', tag.size() + 1);
                    r.status = upper(first.substr(tag.size() + 1, sp == std::string::npos ? std::string::npos : sp - tag.size() - 1));
                    r.text = (sp == std::string::npos) ? "" : first.substr(sp + 1);
                    last_used_ = std::chrono::steady_clock::now();
                    return r;
                }
                if (starts_with(first, "+")) {
                    // AUTHENTICATE 失败时服务器发回挑战，回空行取得 NO 响应；其他命令不会出现
                    if (authenticate) t_.write("\r\n");
                    continue;
                }
                if (starts_with(first, "* BYE")) {
                    dead_ = true;
                    throw std::runtime_error("server closed the connection: " + first);
                }
                if (starts_with(first, "* CAPABILITY ")) caps_ = " " + upper(first.substr(13)) + " ";
                if (handler) handler(segs);
            }
        } catch (...) {
            dead_ = true;
            throw;
        }
    }

private:
    void capability() {
        Tagged r = command("CAPABILITY", nullptr, false);
        if (r.status != "OK") throw std::runtime_error("CAPABILITY failed: " + r.text);
    }

    // 读一条完整响应：行尾是 {n} 时读 n 字节 literal 再继续读下一行
    std::vector<Segment> read_response() {
        std::vector<Segment> segs;
        while (true) {
            std::string line = t_.read_line();
            size_t n = 0;
            bool literal = false;
            if (!line.empty() && line.back() == '}') {
                size_t open = line.rfind('{');
                if (open != std::string::npos) {
                    bool digits = open + 1 < line.size() - 1;
                    for (size_t i = open + 1; i + 1 < line.size(); ++i) {
                        if (line[i] < '0' || line[i] > '9') { digits = false; break; }
                        n = n * 10 + static_cast<size_t>(line[i] - '0');
                    }
                    if (digits) {
                        literal = true;
                        line.erase(open);
                    }
                }
            }
            segs.push_back(Segment{line, false});
            if (!literal) return segs;
            segs.push_back(Segment{t_.read_exact(n), true});
        }
    }

    Config cfg_;
    Transport t_;
    std::string caps_;
    int tag_counter_ = 0;
    bool dead_ = false;
    std::chrono::steady_clock::time_point last_used_{};
};

// ---- 引擎 -------------------------------------------------------------------------

struct BodyResult {
    uint64_t uid;
    std::string body;
    bool ok;
    std::string error;
};

class Engine {
public:
    explicit Engine(Config cfg) : cfg_(std::move(cfg)) {
        py::gil_scoped_release release;
        main_ = std::make_unique<Connection>(cfg_);
        main_->open();
    }

    ~Engine() { close_all(); }

    bool has_capability(const std::string& cap) {
        std::lock_guard<std::mutex> g(mu_);
        return main_ && main_->has_capability(cap);
    }

    void close() {
        py::gil_scoped_release release;
        close_all();
    }

    py::dict fetch_new(uint64_t last_uid, size_t max_messages, py::object on_headers, py::object on_body) {
        std::lock_guard<std::mutex> g(mu_);
        auto started = std::chrono::steady_clock::now();
        std::vector<FetchRecord> records;
        bool gmail = false;
        {
            py::gil_scoped_release release;
            ensure_main();
            close_idle_extras();
            gmail = main_->has_capability("X-GM-EXT-1");
            std::string items = "UID FLAGS INTERNALDATE RFC822.SIZE";
            if (gmail) items += " X-GM-THRID X-GM-LABELS";
            items += " BODY.PEEK[HEADER]";
            std::string cmd = "UID FETCH " + std::to_string(last_uid + 1) + ":* (" + items + ")";
            Tagged r = run_main([&](Connection& c) {
                return c.command(cmd, [&](const std::vector<Segment>& segs) {
                    if (segs.front().text.find(" FETCH ") == std::string::npos) return;
                    FetchRecord rec = parse_fetch(segs);
                    if (rec.uid > last_uid && rec.has_header) records.push_back(std::move(rec));
                }, false);
            });
            if (r.status != "OK") throw std::runtime_error("UID FETCH failed: " + r.text);
        }
        double headers_sec = seconds_since(started);
        py::dict result;
        if (records.size() > max_messages) {
            result["too_many"] = true;
            result["count"] = records.size();
            return result;
        }
        result["too_many"] = false;
        result["count"] = records.size();
        result["headers_sec"] = headers_sec;
        if (records.empty()) {
            result["connections"] = 1;
            result["compressed"] = main_->compressed();
            result["bodies_sec"] = 0.0;
            return result;
        }

        // 头部回调（持 GIL）
        {
            py::list py_records;
            for (const auto& rec : records) {
                py::dict d;
                d["uid"] = rec.uid;
                py::list flags;
                for (const auto& f : rec.flags) flags.append(py::bytes(f));
                d["flags"] = flags;
                d["internaldate"] = py::bytes(rec.internaldate);
                d["size"] = rec.size;
                if (rec.has_thrid) d["thrid"] = rec.thrid; else d["thrid"] = py::none();
                py::list labels;
                for (const auto& l : rec.labels) labels.append(py::bytes(l));
                d["labels"] = labels;
                d["header"] = py::bytes(rec.header);
                py_records.append(d);
            }
            on_headers(py_records);
        }

        // 正文：小邮件在主连接一条命令取齐；大邮件分给副连接
        std::vector<uint64_t> small, large;
        for (const auto& rec : records) (rec.size <= cfg_.small_body_bytes ? small : large).push_back(rec.uid);
        std::vector<std::vector<uint64_t>> extra_jobs;
        int workers = 0;
        {
            py::gil_scoped_release release;
            workers = ensure_extras(static_cast<int>(large.size()));
            extra_jobs.assign(static_cast<size_t>(workers), {});
            for (size_t i = 0; i < large.size(); ++i) {
                if (workers > 0) extra_jobs[i % static_cast<size_t>(workers)].push_back(large[i]);
                else small.push_back(large[i]);   // 副连接都打不开：全部走主连接
            }
        }

        std::mutex qmu;
        std::condition_variable qcv;
        std::deque<BodyResult> queue;
        std::atomic<int> running{0};
        auto push = [&](BodyResult r) {
            std::lock_guard<std::mutex> lg(qmu);
            queue.push_back(std::move(r));
            qcv.notify_one();
        };
        std::vector<std::thread> threads;
        auto fetch_bodies = [&](Connection& conn, const std::vector<uint64_t>& uids, bool one_command) {
            if (uids.empty()) return;
            auto handler = [&](const std::vector<Segment>& segs) {
                if (segs.front().text.find(" FETCH ") == std::string::npos) return;
                FetchRecord rec = parse_fetch(segs);
                if (rec.uid && rec.has_body) push(BodyResult{rec.uid, std::move(rec.body), true, ""});
            };
            auto run_one = [&](const std::string& set) {
                Tagged r = conn.command("UID FETCH " + set + " (UID BODY.PEEK[])", handler, false);
                if (r.status != "OK") throw std::runtime_error("UID FETCH body failed: " + r.text);
            };
            try {
                if (one_command) {
                    std::string set;
                    for (size_t i = 0; i < uids.size(); ++i) {
                        if (i) set += ",";
                        set += std::to_string(uids[i]);
                    }
                    run_one(set);
                } else {
                    for (uint64_t uid : uids) run_one(std::to_string(uid));
                }
            } catch (const std::exception& exc) {
                for (uint64_t uid : uids) push(BodyResult{uid, "", false, exc.what()});
            }
        };
        // 线程结束时的计数：必须定义在函数作用域——线程在下面的代码块结束后仍在运行
        auto finished = [&]() {
            {
                std::lock_guard<std::mutex> lg(qmu);
                --running;
            }
            qcv.notify_one();
        };
        {
            py::gil_scoped_release release;
            running = 1 + workers;
            threads.emplace_back([&]() {
                fetch_bodies(*main_, small, true);
                finished();
            });
            for (int w = 0; w < workers; ++w) {
                threads.emplace_back([&, w]() {
                    fetch_bodies(*extras_[static_cast<size_t>(w)], extra_jobs[static_cast<size_t>(w)], false);
                    finished();
                });
            }
        }

        // 消费队列：每取到一封回调一次（持 GIL），等待时释放 GIL
        std::vector<uint64_t> failed;
        std::string first_error;
        size_t delivered = 0;
        while (true) {
            BodyResult item;
            bool got = false;
            {
                py::gil_scoped_release release;
                std::unique_lock<std::mutex> lk(qmu);
                qcv.wait(lk, [&]() { return !queue.empty() || running.load() == 0; });
                if (!queue.empty()) {
                    item = std::move(queue.front());
                    queue.pop_front();
                    got = true;
                } else if (running.load() == 0) {
                    break;
                }
            }
            if (!got) continue;
            delivered += 1;
            if (!item.ok) {
                failed.push_back(item.uid);
                if (first_error.empty()) first_error = item.error;
                continue;
            }
            try {
                on_body(item.uid, py::bytes(item.body));
            } catch (py::error_already_set&) {
                // Python 回调出错：等线程结束后再抛出
                {
                    py::gil_scoped_release release;
                    for (auto& t : threads) if (t.joinable()) t.join();
                }
                throw;
            }
        }
        {
            py::gil_scoped_release release;
            for (auto& t : threads) if (t.joinable()) t.join();
            // 出错的连接丢弃，下次按需重建
            for (auto& c : extras_) if (c && !c->alive()) c.reset();
            extras_.erase(std::remove_if(extras_.begin(), extras_.end(), [](const std::unique_ptr<Connection>& c) { return !c; }), extras_.end());
        }
        result["connections"] = 1 + workers;
        result["compressed"] = main_->compressed();
        result["bodies_sec"] = seconds_since(started);
        py::list py_failed;
        for (uint64_t u : failed) py_failed.append(u);
        result["failed_uids"] = py_failed;
        if (!failed.empty() && delivered == failed.size()) {
            throw std::runtime_error("body fetch failed: " + first_error);
        }
        return result;
    }

private:
    static double seconds_since(std::chrono::steady_clock::time_point t) {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count();
    }

    void ensure_main() {
        if (!main_ || !main_->alive()) {
            main_ = std::make_unique<Connection>(cfg_);
            main_->open();
        }
    }

    // 主连接上执行；连接已失效（服务器空闲超时断开等）时重连一次再试
    template <typename F>
    Tagged run_main(F fn) {
        try {
            return fn(*main_);
        } catch (const std::exception&) {
            if (!main_->alive()) {
                main_ = std::make_unique<Connection>(cfg_);
                main_->open();
                return fn(*main_);
            }
            throw;
        }
    }

    int ensure_extras(int wanted) {
        int target = std::min(wanted, cfg_.extra_connections);
        while (static_cast<int>(extras_.size()) < target) {
            auto c = std::make_unique<Connection>(cfg_);
            try {
                c->open();
            } catch (const std::exception&) {
                break;   // 打不开（限流等）：用现有的
            }
            extras_.push_back(std::move(c));
        }
        return std::min(target, static_cast<int>(extras_.size()));
    }

    void close_idle_extras() {
        auto now = std::chrono::steady_clock::now();
        for (auto& c : extras_) {
            if (!c) continue;
            auto idle = std::chrono::duration_cast<std::chrono::seconds>(now - c->last_used()).count();
            if (!c->alive() || idle > cfg_.idle_close_sec) {
                try { c->close(); } catch (...) {}
                c.reset();
            }
        }
        extras_.erase(std::remove_if(extras_.begin(), extras_.end(), [](const std::unique_ptr<Connection>& c) { return !c; }), extras_.end());
    }

    void close_all() {
        std::lock_guard<std::mutex> g(mu_);
        for (auto& c : extras_) if (c) { try { c->close(); } catch (...) {} }
        extras_.clear();
        if (main_) { try { main_->close(); } catch (...) {} main_.reset(); }
    }

    Config cfg_;
    std::mutex mu_;
    std::unique_ptr<Connection> main_;
    std::vector<std::unique_ptr<Connection>> extras_;
};

}  // namespace

PYBIND11_MODULE(imapfetch, m) {
    m.doc() = "新邮件下载引擎：常驻 IMAP 连接、头部先行、多连接并发取正文";
    py::class_<Engine>(m, "Engine")
        .def(py::init([](const std::string& host, int port, const std::string& user, const std::string& password,
                         const std::string& token, const std::string& folder, int extra_connections,
                         size_t small_body_bytes, int idle_close_sec, bool compress, int timeout_sec, bool plain_tcp) {
                 Config cfg;
                 cfg.host = host;
                 cfg.port = port;
                 cfg.user = user;
                 cfg.password = password;
                 cfg.token = token;
                 cfg.folder = folder;
                 cfg.extra_connections = extra_connections;
                 cfg.small_body_bytes = small_body_bytes;
                 cfg.idle_close_sec = idle_close_sec;
                 cfg.compress = compress;
                 cfg.timeout_sec = timeout_sec;
                 cfg.plain_tcp = plain_tcp;
                 return new Engine(std::move(cfg));
             }),
             py::arg("host"), py::arg("port"), py::arg("user"), py::arg("password") = "", py::arg("token") = "",
             py::arg("folder") = "INBOX", py::arg("extra_connections") = 2, py::arg("small_body_bytes") = 262144,
             py::arg("idle_close_sec") = 600, py::arg("compress") = true, py::arg("timeout_sec") = 60,
             py::arg("plain_tcp") = false)
        .def("has_capability", &Engine::has_capability, py::arg("cap"))
        .def("close", &Engine::close)
        .def("fetch_new", &Engine::fetch_new, py::arg("last_uid"), py::arg("max_messages"), py::arg("on_headers"), py::arg("on_body"),
             "取 UID 大于 last_uid 的新邮件：先回调 on_headers(records)，再逐封回调 on_body(uid, raw)");
    m.attr("__version__") = "1.0";
}

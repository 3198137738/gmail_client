// fastmime：邮件 MIME 结构解析（Python 扩展，pybind11）。
//
// 只做「结构」这一层：切分头部与正文、按 boundary 拆分 multipart、递归进入 message/rfc822、
// 按 Content-Transfer-Encoding 解码正文、解析 Content-Type / Content-Disposition 的参数
// （含 RFC 2231 分段与编码参数）。头部值原样（展开折行）交给 Python，RFC 2047 解码、
// 字符集转换、地址格式化仍由 Python 完成——这些在 Python 里开销很小，而真正慢的是
// 标准库 email 包逐行解析与头部对象构造，这里替换的正是那部分。
//
// 行为以 Python 标准库 email（policy.default）为准，差异点见各函数注释；
// 任何异常都转为 ValueError 抛回 Python，由调用方退回标准库解析该封邮件。
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <iterator>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

using sv = std::string_view;

constexpr int kMaxDepth = 50;

struct Param {
    std::string value;     // 原始字节（RFC 2231 已拼接并做过百分号解码）
    std::string charset;   // RFC 2231 指定的字符集；为空表示未指定
};

using ParamMap = std::map<std::string, Param>;

struct Header {
    std::string name;      // 小写
    std::string value;     // 展开折行后的原始字节
};

struct Part {
    std::string content_type;   // 小写 type/subtype，缺省 text/plain
    ParamMap ct_params;
    std::string disposition;    // Content-Disposition 原始值（展开折行），可能为空
    bool has_disposition = false;
    ParamMap cd_params;
    std::string content_id;
    bool has_content_id = false;
    std::string payload;        // 按传输编码解码后的字节
};

// ---- 基本工具 --------------------------------------------------------------

inline bool is_ws(char c) { return c == ' ' || c == '\t'; }

std::string lower(sv s) {
    std::string out(s);
    for (auto& c : out) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return out;
}

sv trim(sv s) {
    size_t b = 0, e = s.size();
    while (b < e && (is_ws(s[b]) || s[b] == '\r' || s[b] == '\n')) ++b;
    while (e > b && (is_ws(s[e - 1]) || s[e - 1] == '\r' || s[e - 1] == '\n')) --e;
    return s.substr(b, e - b);
}

// 取 pos 开始的一行：返回不含行尾的内容，next 指向下一行开头（文件末尾无换行时 next == size）
sv next_line(sv data, size_t pos, size_t& next) {
    size_t nl = data.find('\n', pos);
    if (nl == sv::npos) {
        next = data.size();
        return data.substr(pos);
    }
    next = nl + 1;
    size_t end = nl;
    if (end > pos && data[end - 1] == '\r') --end;
    return data.substr(pos, end - pos);
}

// 去掉区间末尾的一个行结束符（multipart 里每个部件正文末尾那个换行属于 boundary）
size_t strip_one_eol(sv data, size_t begin, size_t end) {
    if (end > begin && data[end - 1] == '\n') {
        --end;
        if (end > begin && data[end - 1] == '\r') --end;
    } else if (end > begin && data[end - 1] == '\r') {
        --end;
    }
    return end;
}

// RFC 5322 头部名：可打印 ASCII，不含冒号和空格（与 feedparser 的 headerRE 一致）
bool valid_header_name(sv name) {
    if (name.empty()) return false;
    for (unsigned char c : name) {
        if (c < 0x21 || c > 0x7e || c == ':') return false;
    }
    return true;
}

// ---- 头部 ------------------------------------------------------------------

// 解析从 pos 开始的头部块；返回正文起点。空行结束头部；遇到既不是折行也不是合法头部的行，
// 该行起视为正文（标准库同样处理：MissingHeaderBodySeparatorDefect）。
size_t parse_headers(sv data, size_t pos, std::vector<Header>& out) {
    size_t next = pos;
    bool first = true;
    while (pos < data.size()) {
        sv line = next_line(data, pos, next);
        if (first) {
            first = false;
            if (line.size() > 5 && line.substr(0, 5) == "From ") {   // mbox 信封行，跳过
                pos = next;
                continue;
            }
        }
        if (line.empty()) return next;                       // 空行：正文从下一行开始
        if (is_ws(line[0])) {                                 // 折行：保留续行自己的前导空白
            if (out.empty()) return pos;                      // 没有可续的头部：当作正文
            out.back().value.append(line.data(), line.size());
            pos = next;
            continue;
        }
        size_t colon = line.find(':');
        if (colon == sv::npos || !valid_header_name(line.substr(0, colon))) return pos;
        Header h;
        h.name = lower(line.substr(0, colon));
        sv value = line.substr(colon + 1);
        size_t b = 0;
        while (b < value.size() && is_ws(value[b])) ++b;      // 与 header_source_parse 的 lstrip(' \t') 一致
        h.value.assign(value.data() + b, value.size() - b);
        out.push_back(std::move(h));
        pos = next;
    }
    return data.size();
}

const std::string* find_header(const std::vector<Header>& headers, const char* name) {
    for (const auto& h : headers) {
        if (h.name == name) return &h.value;
    }
    return nullptr;
}

// ---- 参数（Content-Type / Content-Disposition） -----------------------------

// 按分号切分，忽略引号和注释内的分号
std::vector<sv> split_semicolons(sv s) {
    std::vector<sv> out;
    size_t start = 0;
    bool quoted = false;
    int comment = 0;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == '\\' && (quoted || comment) && i + 1 < s.size()) { ++i; continue; }
        if (quoted) { if (c == '"') quoted = false; continue; }
        if (comment) { if (c == '(') ++comment; else if (c == ')') --comment; continue; }
        if (c == '"') { quoted = true; continue; }
        if (c == '(') { comment = 1; continue; }
        if (c == ';') { out.push_back(s.substr(start, i - start)); start = i + 1; }
    }
    out.push_back(s.substr(start));
    return out;
}

std::string unquote(sv v) {
    v = trim(v);
    if (v.size() >= 2 && v.front() == '"' && v.back() == '"') {
        std::string out;
        out.reserve(v.size());
        for (size_t i = 1; i + 1 < v.size(); ++i) {
            if (v[i] == '\\' && i + 2 < v.size()) { out.push_back(v[i + 1]); ++i; }
            else out.push_back(v[i]);
        }
        return out;
    }
    return std::string(v);
}

int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

std::string percent_decode(sv s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size()) {
            int hi = hexval(s[i + 1]), lo = hexval(s[i + 2]);
            if (hi >= 0 && lo >= 0) { out.push_back(static_cast<char>(hi * 16 + lo)); i += 2; continue; }
        }
        out.push_back(s[i]);
    }
    return out;
}

struct Segment {
    int index;          // RFC 2231 分段号，-1 表示未分段
    bool encoded;       // 名字以 * 结尾：charset'lang'percent-encoded
    std::string value;
};

// 解析 "type/subtype; a=b; c*0*=..." 中分号之后的参数。RFC 2231 分段按序号拼接；
// 编码分段做百分号解码，字符集取自第一个编码分段。
ParamMap parse_params(const std::vector<sv>& pieces) {
    std::map<std::string, std::vector<Segment>> grouped;
    std::map<std::string, std::string> charsets;
    for (size_t i = 1; i < pieces.size(); ++i) {
        sv piece = trim(pieces[i]);
        if (piece.empty()) continue;
        size_t eq = piece.find('=');
        if (eq == sv::npos) continue;
        std::string name = lower(trim(piece.substr(0, eq)));
        sv rawval = piece.substr(eq + 1);
        Segment seg{-1, false, ""};
        if (!name.empty() && name.back() == '*') { seg.encoded = true; name.pop_back(); }
        size_t star = name.rfind('*');
        if (star != std::string::npos) {
            sv digits = sv(name).substr(star + 1);
            bool all_digits = !digits.empty() && std::all_of(digits.begin(), digits.end(), [](char c) { return c >= '0' && c <= '9'; });
            if (all_digits) {
                seg.index = std::atoi(std::string(digits).c_str());
                name.resize(star);
            }
        }
        std::string value = unquote(rawval);
        if (seg.encoded) {
            // charset'language'encoded —— 只有第一段（或未分段）带 charset 前缀
            size_t q1 = value.find('\'');
            size_t q2 = (q1 == std::string::npos) ? std::string::npos : value.find('\'', q1 + 1);
            if (q2 != std::string::npos && (seg.index <= 0)) {
                std::string cs = lower(sv(value).substr(0, q1));
                if (!cs.empty() && !charsets.count(name)) charsets[name] = cs;
                value = value.substr(q2 + 1);
            }
            value = percent_decode(value);
        }
        seg.value = std::move(value);
        grouped[name].push_back(std::move(seg));
    }
    ParamMap out;
    for (auto& [name, segs] : grouped) {
        std::stable_sort(segs.begin(), segs.end(), [](const Segment& a, const Segment& b) { return a.index < b.index; });
        Param p;
        bool any_encoded = false;
        for (const auto& s : segs) { p.value += s.value; any_encoded = any_encoded || s.encoded; }
        if (any_encoded) {
            auto it = charsets.find(name);
            p.charset = (it != charsets.end()) ? it->second : "us-ascii";
        }
        out[name] = std::move(p);
    }
    return out;
}

bool is_token(sv s) {
    if (s.empty()) return false;
    for (unsigned char c : s) {
        if (c <= 0x20 || c >= 0x7f) return false;
        switch (c) {
            case '(': case ')': case '<': case '>': case '@': case ',': case ';': case ':':
            case '\\': case '"': case '/': case '[': case ']': case '?': case '=':
                return false;
            default: break;
        }
    }
    return true;
}

// Content-Type 值 → (小写 type/subtype, 参数)。无效时与标准库一致：text/plain
std::pair<std::string, ParamMap> parse_content_type(sv value, const std::string& fallback) {
    auto pieces = split_semicolons(value);
    std::string ctype = lower(trim(pieces.empty() ? sv() : pieces[0]));
    size_t slash = ctype.find('/');
    bool valid = slash != std::string::npos && is_token(sv(ctype).substr(0, slash)) && is_token(sv(ctype).substr(slash + 1));
    ParamMap params = parse_params(pieces);
    if (!valid) return {fallback, params};
    return {ctype, params};
}

// ---- 传输编码 ----------------------------------------------------------------

// 与 email 包 get_payload(decode=True) 的宽松 base64 行为一致（email._encoded_words.decode_b）：
// 忽略空白、"=" 和其他非字母表字符，把全部数据字符按 4 个一组解码，末尾 2 个→1 字节、
// 3 个→2 字节；数据字符数除 4 余 1 时无法解码，原样返回（去掉换行）。
std::string decode_base64(sv s) {
    static int8_t table[256];
    static bool init = false;
    if (!init) {
        std::fill(std::begin(table), std::end(table), static_cast<int8_t>(-1));
        const char* alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for (int i = 0; i < 64; ++i) table[static_cast<unsigned char>(alpha[i])] = static_cast<int8_t>(i);
        init = true;
    }
    size_t count = 0;
    for (unsigned char c : s) if (table[c] >= 0) ++count;
    if (count % 4 == 1) {
        std::string out;
        out.reserve(s.size());
        for (char c : s) if (c != '\r' && c != '\n') out.push_back(c);
        return out;
    }
    std::string out;
    out.reserve(count / 4 * 3 + 3);
    uint32_t acc = 0;
    int n = 0;
    for (unsigned char c : s) {
        int8_t v = table[c];
        if (v < 0) continue;
        acc = (acc << 6) | static_cast<uint32_t>(v);
        if (++n == 4) {
            out.push_back(static_cast<char>((acc >> 16) & 0xff));
            out.push_back(static_cast<char>((acc >> 8) & 0xff));
            out.push_back(static_cast<char>(acc & 0xff));
            acc = 0; n = 0;
        }
    }
    if (n == 2) out.push_back(static_cast<char>((acc >> 4) & 0xff));
    else if (n == 3) {
        out.push_back(static_cast<char>((acc >> 10) & 0xff));
        out.push_back(static_cast<char>((acc >> 2) & 0xff));
    }
    return out;
}

// 与 quopri.decodestring（binascii.a2b_qp）一致：=XX 解码、"=" 后接换行为软换行、
// 其他 "=" 原样保留；行尾空白不去除。
std::string decode_qp(sv s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c != '=') { out.push_back(c); continue; }
        if (i + 1 >= s.size()) { out.push_back('='); continue; }
        char a = s[i + 1];
        if (a == '\n') { i += 1; continue; }
        if (a == '\r') { i += 1; if (i + 1 < s.size() && s[i + 1] == '\n') ++i; continue; }
        if (i + 2 < s.size()) {
            int hi = hexval(a), lo = hexval(s[i + 2]);
            if (hi >= 0 && lo >= 0) { out.push_back(static_cast<char>(hi * 16 + lo)); i += 2; continue; }
        }
        out.push_back('=');
    }
    return out;
}

std::string decode_payload(sv body, const std::string* cte) {
    if (!cte) return std::string(body);
    std::string enc = lower(trim(*cte));
    if (enc == "base64") return decode_base64(body);
    if (enc == "quoted-printable") return decode_qp(body);
    return std::string(body);
}

// ---- 实体（递归） ------------------------------------------------------------

void parse_entity(sv data, size_t begin, size_t end, const std::string& default_ctype,
                  std::vector<Part>& parts, int depth);

// boundary 行：--boundary，可带结束符 --，之后只允许空白
bool match_boundary(sv line, const std::string& delimiter, bool& is_end) {
    if (line.size() < delimiter.size() || line.substr(0, delimiter.size()) != delimiter) return false;
    sv rest = line.substr(delimiter.size());
    is_end = rest.size() >= 2 && rest.substr(0, 2) == "--";
    if (is_end) rest = rest.substr(2);
    for (char c : rest) if (!is_ws(c)) return false;
    return true;
}

void parse_multipart(sv data, size_t body_begin, size_t end, const std::string& boundary,
                     const std::string& ctype, std::vector<Part>& parts, int depth) {
    std::string delimiter = "--" + boundary;
    size_t pos = body_begin;
    size_t next = pos;
    bool in_part = false;
    size_t part_begin = 0;
    std::string child_default = (ctype == "multipart/digest") ? "message/rfc822" : "text/plain";
    while (pos < end) {
        sv line = next_line(data.substr(0, end), pos, next);
        bool is_end = false;
        if (match_boundary(line, delimiter, is_end)) {
            if (in_part) {
                size_t part_end = strip_one_eol(data, part_begin, pos);
                parse_entity(data, part_begin, part_end, child_default, parts, depth + 1);
            }
            if (is_end) return;
            in_part = true;
            part_begin = next;
        }
        pos = next;
    }
    if (in_part) {
        // 没有结束 boundary：最后一个部件直到末尾（标准库同样去掉末尾一个换行）
        size_t part_end = strip_one_eol(data, part_begin, end);
        parse_entity(data, part_begin, part_end, child_default, parts, depth + 1);
    } else {
        // 从未出现 boundary：整个正文作为一个部件（标准库：get_payload 为字符串，walk 产出自身）
        Part p;
        p.content_type = ctype;
        p.payload.assign(data.data() + body_begin, end - body_begin);
        parts.push_back(std::move(p));
    }
}

void parse_entity(sv data, size_t begin, size_t end, const std::string& default_ctype,
                  std::vector<Part>& parts, int depth) {
    if (depth > kMaxDepth) throw std::runtime_error("MIME nesting too deep");
    std::vector<Header> headers;
    size_t body_begin = parse_headers(data.substr(0, end), begin, headers);
    if (body_begin > end) body_begin = end;

    std::string ctype = default_ctype;
    ParamMap ct_params;
    if (const std::string* ct = find_header(headers, "content-type")) {
        auto parsed = parse_content_type(*ct, "text/plain");
        ctype = std::move(parsed.first);
        ct_params = std::move(parsed.second);
    }

    if (ctype.rfind("multipart/", 0) == 0) {
        auto it = ct_params.find("boundary");
        if (it != ct_params.end() && !it->second.value.empty()) {
            parse_multipart(data, body_begin, end, it->second.value, ctype, parts, depth);
            return;
        }
        // 没有 boundary：整个正文作为一个部件
        Part p;
        p.content_type = ctype;
        p.ct_params = std::move(ct_params);
        p.payload.assign(data.data() + body_begin, end - body_begin);
        parts.push_back(std::move(p));
        return;
    }
    if (ctype == "message/rfc822") {
        // 标准库 walk() 会进入附带的邮件：它的部件同样成为叶子
        parse_entity(data, body_begin, end, "text/plain", parts, depth + 1);
        return;
    }

    Part p;
    p.content_type = std::move(ctype);
    p.ct_params = std::move(ct_params);
    if (const std::string* cd = find_header(headers, "content-disposition")) {
        p.has_disposition = true;
        p.disposition = *cd;
        auto pieces = split_semicolons(*cd);
        p.cd_params = parse_params(pieces);
    }
    if (const std::string* cid = find_header(headers, "content-id")) {
        p.has_content_id = true;
        p.content_id = *cid;
    }
    p.payload = decode_payload(data.substr(body_begin, end - body_begin), find_header(headers, "content-transfer-encoding"));
    parts.push_back(std::move(p));
}

// ---- 转成 Python 对象 --------------------------------------------------------

py::dict params_to_py(const ParamMap& params) {
    py::dict out;
    for (const auto& [name, p] : params) {
        py::object charset = py::none();
        if (!p.charset.empty()) charset = py::str(p.charset);
        out[py::str(name)] = py::make_tuple(py::bytes(p.value), charset);
    }
    return out;
}

py::dict parse(const py::bytes& raw) {
    const std::string buffer = static_cast<std::string>(raw);
    const sv data(buffer);
    std::vector<Header> headers;
    size_t body_begin = parse_headers(data, 0, headers);
    std::vector<Part> parts;

    // 顶层：与 parse_entity 相同，但头部已解析（避免重复扫描）
    std::string ctype = "text/plain";
    ParamMap ct_params;
    if (const std::string* ct = find_header(headers, "content-type")) {
        auto parsed = parse_content_type(*ct, "text/plain");
        ctype = std::move(parsed.first);
        ct_params = std::move(parsed.second);
    }
    if (ctype.rfind("multipart/", 0) == 0) {
        auto it = ct_params.find("boundary");
        if (it != ct_params.end() && !it->second.value.empty()) {
            parse_multipart(data, body_begin, data.size(), it->second.value, ctype, parts, 1);
        } else {
            Part p;
            p.content_type = ctype;
            p.ct_params = ct_params;
            p.payload.assign(data.data() + body_begin, data.size() - body_begin);
            parts.push_back(std::move(p));
        }
    } else if (ctype == "message/rfc822") {
        parse_entity(data, body_begin, data.size(), "text/plain", parts, 1);
    } else {
        Part p;
        p.content_type = ctype;
        p.ct_params = ct_params;
        if (const std::string* cd = find_header(headers, "content-disposition")) {
            p.has_disposition = true;
            p.disposition = *cd;
            p.cd_params = parse_params(split_semicolons(*cd));
        }
        if (const std::string* cid = find_header(headers, "content-id")) {
            p.has_content_id = true;
            p.content_id = *cid;
        }
        p.payload = decode_payload(data.substr(body_begin), find_header(headers, "content-transfer-encoding"));
        parts.push_back(std::move(p));
    }

    py::list py_headers;
    for (const auto& h : headers) py_headers.append(py::make_tuple(py::str(h.name), py::bytes(h.value)));
    py::list py_parts;
    for (const auto& p : parts) {
        py::dict d;
        d["content_type"] = py::str(p.content_type);
        d["ct_params"] = params_to_py(p.ct_params);
        py::object disposition = py::none();
        if (p.has_disposition) disposition = py::bytes(p.disposition);
        py::object content_id = py::none();
        if (p.has_content_id) content_id = py::bytes(p.content_id);
        d["disposition"] = disposition;
        d["cd_params"] = params_to_py(p.cd_params);
        d["content_id"] = content_id;
        d["payload"] = py::bytes(p.payload);
        py_parts.append(d);
    }
    py::dict result;
    result["headers"] = py_headers;
    result["parts"] = py_parts;
    return result;
}

}  // namespace

PYBIND11_MODULE(fastmime, m) {
    m.doc() = "邮件 MIME 结构解析（头部展开、multipart 拆分、传输编码解码、RFC 2231 参数）";
    m.def("parse", [](py::bytes raw) -> py::dict {
        try {
            return parse(raw);
        } catch (const py::error_already_set&) {
            throw;
        } catch (const std::exception& exc) {
            throw py::value_error(std::string("fastmime: ") + exc.what());
        } catch (...) {
            throw py::value_error("fastmime: unknown error");
        }
    }, py::arg("raw"), "解析原始邮件字节，返回 {'headers': [(name, bytes), ...], 'parts': [...]}");
    m.attr("__version__") = "1.0";
}

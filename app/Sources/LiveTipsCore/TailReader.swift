import Foundation

/// Reads only the lines appended to a file since the previous call.
/// PB-30 (review round 6): a file replaced by another one (the recorder's end-of-call rewrite with the translations,
/// the speaker names after the call) is told by its identity, not only by a smaller size: a longer new file used to be
/// read from the old offset, inside a row. After a replacement the whole new file is returned and `replaced` is set,
/// so the caller shows it instead of appending it.
public final class TailReader {
    public let path: String
    private var offset: UInt64 = 0
    private var buffer = Data()
    private var fileID: UInt64?
    /// The last `newLines()` started again from the start of a new file: its lines are the whole file.
    public private(set) var replaced = false

    public init(path: String) { self.path = path }

    public func newLines() -> [String] {
        replaced = false
        guard let fh = FileHandle(forReadingAtPath: path) else { return [] }
        defer { try? fh.close() }
        var st = stat()
        let id: UInt64? = fstat(fh.fileDescriptor, &st) == 0 ? UInt64(st.st_ino) : nil   // the file this handle reads
        let size = (try? fh.seekToEnd()) ?? 0
        if size < offset || (fileID != nil && id != nil && id != fileID) {   // truncated or replaced
            offset = 0
            buffer = Data()
            replaced = true
        }
        if id != nil { fileID = id }
        if size == offset { return [] }
        try? fh.seek(toOffset: offset)
        let data = fh.readDataToEndOfFile()
        offset += UInt64(data.count)
        buffer.append(data)
        var lines: [String] = []
        while let r = buffer.range(of: Data([0x0A])) {
            let lineData = buffer.subdata(in: buffer.startIndex..<r.lowerBound)
            buffer.removeSubrange(buffer.startIndex..<r.upperBound)
            if let s = String(data: lineData, encoding: .utf8),
               !s.trimmingCharacters(in: .whitespaces).isEmpty {
                lines.append(s)
            }
        }
        return lines
    }
}

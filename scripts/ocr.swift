// OCR every screenshot with Apple Vision and write data/ocr.json.
//
// Usage: swift scripts/ocr.swift [screenshotsDir] [outputJson]
// Defaults: screenshots/  ->  data/ocr.json
//
// Already-processed files (same name + size + mtime) are skipped, so adding
// new screenshots later only OCRs the new ones.

import Foundation
import Vision
import ImageIO

let fm = FileManager.default
let args = CommandLine.arguments
let scriptDir = URL(fileURLWithPath: args[0]).deletingLastPathComponent()
let root = scriptDir.deletingLastPathComponent()
let inputDir = URL(fileURLWithPath: args.count > 1 ? args[1] : root.appendingPathComponent("screenshots").path)
let outputFile = URL(fileURLWithPath: args.count > 2 ? args[2] : root.appendingPathComponent("data/ocr.json").path)

let imageExts: Set<String> = ["jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "tif", "gif", "bmp"]

// Load the existing JSON so unchanged files are skipped.
var results: [String: [String: Any]] = [:]
if let data = try? Data(contentsOf: outputFile),
   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] {
    results = obj
}

func fingerprint(_ url: URL) -> String {
    let attrs = (try? fm.attributesOfItem(atPath: url.path)) ?? [:]
    let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
    let mtime = (attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
    return "\(size):\(Int(mtime))"
}

func recognize(_ url: URL) throws -> [String] {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "ocr", code: 1, userInfo: [NSLocalizedDescriptionKey: "cannot decode image"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    let observations = request.results ?? []
    // Vision returns observations roughly top-to-bottom; sort explicitly (y is bottom-up).
    let sorted = observations.sorted { $0.boundingBox.midY > $1.boundingBox.midY }
    return sorted.compactMap { $0.topCandidates(1).first?.string }
}

guard let entries = try? fm.contentsOfDirectory(at: inputDir, includingPropertiesForKeys: nil) else {
    FileHandle.standardError.write("Cannot read \(inputDir.path)\n".data(using: .utf8)!)
    exit(1)
}

let images = entries
    .filter { imageExts.contains($0.pathExtension.lowercased()) }
    .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }

var processed = 0, skipped = 0, failed = 0
for url in images {
    let name = url.lastPathComponent
    let fp = fingerprint(url)
    if let existing = results[name], existing["fingerprint"] as? String == fp {
        skipped += 1
        continue
    }
    do {
        let lines = try recognize(url)
        results[name] = ["fingerprint": fp, "lines": lines, "text": lines.joined(separator: "\n")]
        processed += 1
        print("OCR  \(name)  (\(lines.count) lines)")
    } catch {
        results[name] = ["fingerprint": fp, "lines": [], "text": "", "error": "\(error.localizedDescription)"]
        failed += 1
        FileHandle.standardError.write("FAIL \(name): \(error.localizedDescription)\n".data(using: .utf8)!)
    }
}

// Drop entries for screenshots that no longer exist.
let present = Set(images.map { $0.lastPathComponent })
for key in results.keys where !present.contains(key) { results.removeValue(forKey: key) }

try? fm.createDirectory(at: outputFile.deletingLastPathComponent(), withIntermediateDirectories: true)
let json = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
try json.write(to: outputFile)
print("done: \(processed) processed, \(skipped) cached, \(failed) failed -> \(outputFile.path)")

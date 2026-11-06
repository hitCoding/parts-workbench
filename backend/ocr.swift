import Foundation
import Vision
import ImageIO
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fputs("Cannot read image\n", stderr); exit(1)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: image).perform([request])
let lines = (request.results ?? []).sorted {
    if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.015 { return $0.boundingBox.midY > $1.boundingBox.midY }
    return $0.boundingBox.minX < $1.boundingBox.minX
}.compactMap { observation -> [String: Any]? in
    guard let top = observation.topCandidates(1).first else { return nil }
    return ["text": top.string, "confidence": top.confidence]
}
let data = try JSONSerialization.data(withJSONObject: lines)
FileHandle.standardOutput.write(data)

// carry-model: Apple's on-device model, the fallback that summarises a meeting when no
// assistant of the owner answers (Claude Code, Codex). It reads {"instructions", "prompt"}
// as JSON on stdin and writes {"items": [...]} on stdout, the shape meet.py asks every
// assistant for. Nothing leaves this Mac. `carry app install` builds it next to the app.
import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

struct Request: Decodable {
    let instructions: String
    let prompt: String
}

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
@Generable struct Entry {
    @Guide(description: "one self-contained sentence")
    var statement: String
    @Guide(description: "the number of the transcript line the quote comes from")
    var line: Int
    @Guide(description: "4 to 25 words copied exactly from that line")
    var quote: String
    @Guide(description: "3 to 6 short search terms", .maximumCount(6))
    var keywords: [String]
}

// One list per kind: asked for one "type" field, the model labelled nearly everything a
// decision or a summary and left open items out (measured).
@available(macOS 26.0, *)
@Generable struct Notes {
    @Guide(description: "the main points, in order")
    var summary: [Entry]
    @Guide(description: "what the meeting decided or agreed")
    var decisions: [Entry]
    @Guide(description: "work still to do: who does what, by when")
    var openItems: [Entry]
    @Guide(description: "durable facts: numbers, dates, names, results")
    var facts: [Entry]
}

struct Item: Encodable {
    let type: String
    let statement: String
    let line: Int
    let quote: String
    let keywords: [String]
}

@available(macOS 26.0, *)
func items(_ notes: Notes) -> [String: [Item]] {
    let kinds = [("summary", notes.summary), ("decision", notes.decisions), ("open_item", notes.openItems), ("fact", notes.facts)]
    return ["items": kinds.flatMap { kind, entries in
        entries.map { Item(type: kind, statement: $0.statement, line: $0.line, quote: $0.quote, keywords: $0.keywords) }
    }]
}
#endif

@main struct CarryModel {
    static func main() async {
        guard let data = try? FileHandle.standardInput.readToEnd(),
              let request = try? JSONDecoder().decode(Request.self, from: data) else { fail("bad_request", 64) }
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            let model = SystemLanguageModel.default
            if case .unavailable(let why) = model.availability { fail("apple_model_unavailable: \(why)", 69) }
            let session = LanguageModelSession(model: model, instructions: request.instructions)
            do {
                // Greedy: sampled answers swung from 13 items to 3 on the same transcript (measured).
                let reply = try await session.respond(to: request.prompt, generating: Notes.self,
                                                      options: GenerationOptions(samplingMode: .greedy))
                FileHandle.standardOutput.write(try JSONEncoder().encode(items(reply.content)))
                exit(0)
            } catch let error as LanguageModelSession.GenerationError {
                // A part too long for the model's window: meet.py halves it and asks again.
                if case .exceededContextWindowSize = error { fail("context_window_exceeded", 70) }
                fail("\(error)", 1)
            } catch {
                fail("\(error)", 1)
            }
        }
        #endif
        fail("apple_model_unavailable: macOS 26 or newer is needed", 69)
    }
}

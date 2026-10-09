import EventKit
import Foundation

struct Request: Decodable {
    let start: TimeInterval
    let end: TimeInterval
    let output: String
}

struct Interval: Encodable {
    let start: String
    let end: String
}

struct Result: Encodable {
    var intervals: [Interval]?
    var error: String?
}

final class AccessBox: @unchecked Sendable {
    var done = false
    var granted = false
    var message = ""
}

let bundleURL = Bundle.main.bundleURL
let requestURL = bundleURL.deletingLastPathComponent().appendingPathComponent("calendar-request.json")

func finish(_ result: Result, to url: URL, code: Int32) -> Never {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let data = (try? encoder.encode(result)) ?? Data("{\"error\":\"Could not write calendar result.\"}".utf8)
    try? data.write(to: url, options: .atomic)
    exit(code)
}

guard let requestData = try? Data(contentsOf: requestURL),
      let request = try? JSONDecoder().decode(Request.self, from: requestData) else {
    fputs("Missing calendar request.\n", stderr)
    exit(2)
}

let outputURL = URL(fileURLWithPath: request.output)
let box = AccessBox()
let store = EKEventStore()
store.requestFullAccessToEvents { granted, error in
    box.granted = granted
    box.message = error?.localizedDescription ?? ""
    box.done = true
}

let deadline = Date().addingTimeInterval(120)
while !box.done && Date() < deadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
}
if !box.done {
    finish(Result(error: "Timed out waiting for Calendar access."), to: outputURL, code: 1)
}
if !box.granted {
    var message = "Calendar access was denied. Allow goodmoviefinder in System Settings → Privacy & Security → Calendars, then run the command again."
    if !box.message.isEmpty {
        message += " (\(box.message))"
    }
    finish(Result(error: message), to: outputURL, code: 1)
}

let windowStart = Date(timeIntervalSince1970: request.start)
let windowEnd = Date(timeIntervalSince1970: request.end)
let predicate = store.predicateForEvents(withStart: windowStart, end: windowEnd, calendars: nil)
let events = store.events(matching: predicate)
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime]
var intervals: [Interval] = []
for event in events {
    if event.isAllDay { continue }
    if event.availability == .free { continue }
    if event.status == .canceled { continue }
    if let attendees = event.attendees,
       attendees.contains(where: { $0.isCurrentUser && $0.participantStatus == .declined }) {
        continue
    }
    guard let start = event.startDate, let end = event.endDate, end > start else { continue }
    intervals.append(Interval(start: formatter.string(from: start), end: formatter.string(from: end)))
}
finish(Result(intervals: intervals), to: outputURL, code: 0)

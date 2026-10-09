import EventKit
import Foundation

struct Request: Decodable {
    let start: TimeInterval
    let end: TimeInterval
    let output: String
    let events: [PlanEvent]?
}

struct PlanEvent: Decodable {
    let title: String
    let start: TimeInterval
    let end: TimeInterval
    let location: String
    let url: String
    let key: String
}

struct Interval: Encodable {
    let start: String
    let end: String
}

struct Result: Encodable {
    var intervals: [Interval]?
    var error: String?
    var added: Int?
    var skipped: Int?
    var calendar: String?
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

if let plan = request.events {
    guard let calendar = store.defaultCalendarForNewEvents else {
        finish(Result(error: "Apple Calendar has no calendar for new events."), to: outputURL, code: 1)
    }
    var added = 0
    var skipped = 0
    for item in plan {
        let start = Date(timeIntervalSince1970: item.start)
        let end = Date(timeIntervalSince1970: item.end)
        let marker = "goodmoviefinder:\(item.key)"
        let existing = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: [calendar]))
        if existing.contains(where: { ($0.notes ?? "").contains(marker) }) {
            skipped += 1
            continue
        }
        let event = EKEvent(eventStore: store)
        event.calendar = calendar
        event.title = item.title
        event.startDate = start
        event.endDate = end
        event.location = item.location
        event.notes = marker
        event.availability = .busy
        if item.url.hasPrefix("http"), let link = URL(string: item.url) {
            event.url = link
        }
        do {
            try store.save(event, span: .thisEvent)
            added += 1
        } catch {
            finish(Result(error: "Could not save a film. \(error.localizedDescription)"), to: outputURL, code: 1)
        }
    }
    finish(
        Result(added: added, skipped: skipped, calendar: calendar.title),
        to: outputURL,
        code: 0
    )
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

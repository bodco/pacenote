import Foundation
import LiveTipsCore

/// PB-109 (owner 25.11): the call series picker, one check group per acceptance line.
func callSeriesPB109() {
    typealias B = CallSeriesBook
    let d0 = Date(timeIntervalSince1970: 1_790_000_000)
    func day(_ n: Double) -> Date { d0.addingTimeInterval(n * 86400) }
    func call(_ stem: String, _ title: String, _ n: Double, _ project: String = "Orbit", mins: Double? = 30) -> B.Call {
        B.Call(file: "/w/\(project)/live/2026-11-0\(Int(n))-1000-\(stem).app.md", title: title, project: project, start: day(n), minutes: mins)
    }
    let s1 = call("weekly-sync", "Weekly sync", 1), s2 = call("weekly-sync", "Weekly sync", 3)
    let kick = call("kickoff", "Kickoff with Mike", 2), demo = call("demo", "Product demo", 4)
    let other = call("weekly-sync", "Weekly sync", 5, mins: nil).with(project: "Northpoint")
    let all = [s1, s2, kick, demo, other]

    // 3. Today's rule unchanged: same project and title join by themselves, never another project's call
    var b = B()
    expect(b.members(of: s2, in: all).map(\.key) == [s2.key, s1.key], "PB-109 3: same project and title join automatically, newest first")
    expect(!b.members(of: s2, in: all).contains { $0.project == "Northpoint" }, "PB-109 7: the same title in another project is not in the series")
    expect(B.titleKey("Weekly Sync #2") == CommitmentRegister.seriesKey(project: "", title: "Weekly Sync #2").dropFirst(), "PB-109 9: the title key is the register's series key")
    // 3. Undo of an automatic join
    b.takeOut(s1.file, project: "Orbit")
    expect(b.members(of: s2, in: all).map(\.key) == [s2.key], "PB-109 3: a call taken out of its automatic series leaves it")
    expect(b.members(of: s1, in: all).map(\.key) == [s1.key], "PB-109 3: the call taken out is alone")
    b = B()

    // 1. The picker: same project only, newest first, search by title or date
    let cands = B.candidates(all, project: "Orbit", excluding: [s2.key])
    expect(cands.map(\.key) == [demo.key, kick.key, s1.key], "PB-109 1: candidates newest first, without the excluded calls (\(cands.map(\.title)))")
    expect(cands.first?.minutes == 30 && cands.first?.title == "Product demo", "PB-109 1: a candidate carries its title, date and length")
    expect(cands.allSatisfy { $0.project == "Orbit" }, "PB-109 7: another project's calls are never offered")
    expect(B.candidates(all, project: "Orbit", excluding: [], query: "kickoff").map(\.key) == [kick.key], "PB-109 1: the search box filters by title")
    let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
    expect(B.candidates(all, project: "Orbit", excluding: [], query: f.string(from: demo.start)).map(\.key) == [demo.key], "PB-109 1: the search box filters by date")

    // 2. A manual series: editable name, one series per call, take a call out
    let a = b.create(project: "Orbit", name: "Orbit weekly", title: s2.title, id: "s-a")
    b.add(kick.file, to: a.id)
    expect(Set(b.members(a, in: all).map(\.key)) == [s1.key, s2.key, kick.key] && b.members(a, in: all).first?.key == s2.key,
           "PB-109 2: the series holds its same-title calls and the picked one")
    b.rename(a.id, to: "Orbit weekly with Mike")
    expect(b.manual(id: a.id)?.name == "Orbit weekly with Mike", "PB-109 2: the name is editable")
    b.rename(a.id, to: "  ")
    expect(b.manual(id: a.id)?.name == "Orbit weekly with Mike", "PB-109 2: an empty name is not saved")
    let c = b.create(project: "Orbit", name: "Demos", title: demo.title, id: "s-c")
    b.add(kick.file, to: c.id)
    expect(b.manual(file: kick.file, title: kick.title, project: "Orbit")?.id == c.id && !b.members(a, in: all).contains { $0.key == kick.key },
           "PB-109 2: a call belongs to one series at most (picking it elsewhere moves it)")
    b.takeOut(kick.file, project: "Orbit")
    expect(b.manual(file: kick.file, title: kick.title, project: "Orbit") == nil && b.members(of: kick, in: all).map(\.key) == [kick.key],
           "PB-109 2: a call can be taken out")
    b.add(kick.file, to: a.id)
    expect(b.manual(file: kick.file, title: kick.title, project: "Orbit")?.id == a.id && !b.isAlone(kick.file, project: "Orbit"),
           "PB-109 2: a call taken out can be picked again")
    // the same title in another project never joins a manual series
    expect(b.manual(file: other.file, title: other.title, project: "Northpoint") == nil && !b.members(a, in: all).contains { $0.project == "Northpoint" },
           "PB-109 7: a manual series never mixes in another project's call")

    // 4. A call still to come with the series' title gets the whole series as its earlier calls
    expect(Set(b.members(title: "Weekly sync", project: "Orbit", in: all).map(\.key)) == [s1.key, s2.key, kick.key],
           "PB-109 4: call setup of the series' title sees every call of the manual series")
    expect(b.members(title: "Weekly sync", project: "Northpoint", in: all).map(\.key) == [other.key], "PB-109 7: another project keeps its own series")
    expect(b.titles(title: "Weekly sync", project: "Orbit") == ["weeklysync"], "PB-109 4: the series' titles")

    // 5. Commitments follow the calls: adding an old call brings its items, taking it out removes them
    // the register as the engine and the skill write it (op add), folded by the register's own reader
    func add(_ id: String, _ text: String, owner: String = "me", series: String, call: String) -> [String: Any] {
        ["op": "add", "id": id, "text": text, "owner": owner, "project": "orbit", "series": series, "call": call, "created": "", "by": "engine"]
    }
    func ev(_ id: String, _ cl: B.Call) -> [String: Any] {
        add(id, "item " + id, series: CommitmentRegister.seriesKey(project: "orbit", title: cl.title),
            call: (cl.file as NSString).lastPathComponent.replacingOccurrences(of: ".app", with: ""))
    }
    let folded = CommitmentRegister.fold([ev("c1", s1), ev("c2", s2), ev("ck", kick), ev("cd", demo),
                                          add("cs", "from the skill", owner: "Emily", series: "orbit|weeklysync", call: "2026-10-01-1000-weekly-sync.md")])
    let items = folded.filter { $0.id != "cs" }
    guard let skillOnly = folded.first(where: { $0.id == "cs" }), items.count == 4 else { expect(false, "PB-109: the register folds"); return }
    func shown(_ book: B) -> Set<String> {
        Set(book.items(items + [skillOnly], members: book.members(title: "Weekly sync", project: "Orbit", in: all) + [s2],
                       titles: book.titles(title: "Weekly sync", project: "Orbit"), project: "Orbit").map(\.id))
    }
    expect(shown(b) == ["c1", "c2", "ck", "cs"], "PB-109 5: a picked call's items join the series' list at once (\(shown(b)))")
    var b2 = b; b2.takeOut(kick.file, project: "Orbit")
    expect(shown(b2) == ["c1", "c2", "cs"], "PB-109 5: taking a call out removes its items (\(shown(b2)))")
    b2.takeOut(s1.file, project: "Orbit")
    expect(!shown(b2).contains("c1"), "PB-109 5: taking out a same-title call removes its items too")
    expect(items.allSatisfy { $0.series.hasPrefix("orbit|") } && skillOnly.series == "orbit|weeklysync", "PB-109 9: no item's series key is rewritten")

    // 9. Old keys: with no book entries the series' items are the old ones (series key match), nothing more
    let empty = B()
    let old = Set(empty.items(items + [skillOnly], members: empty.members(of: s2, in: all), titles: [B.titleKey(s2.title)], project: "Orbit").map(\.id))
    expect(old == ["c1", "c2", "cs"], "PB-109 9: an empty book shows exactly the series-key items (\(old))")
    if let data = try? JSONEncoder().encode(b), let back = try? JSONDecoder().decode(B.self, from: data) {
        expect(back == b, "PB-109 9: the book survives a save and a load")
    } else { expect(false, "PB-109: the book encodes") }

    // 6. Suggestions: same project, similar title or same people; never added by themselves
    let pool = B.candidates(all, project: "Orbit", excluding: Set(b.members(a, in: all).map(\.key)) )
    let people: [String: [String]] = [demo.key: ["Sarah Lee"], kick.key: ["Mike Turner"]]
    let sug = B.suggested(pool + [call("sync-notes", "Weekly sync notes", 6)], project: "Orbit", titles: ["Weekly sync"], people: ["mike turner"], peopleOf: { people[$0.key] ?? [] })
    expect(sug.map(\.title) == ["Weekly sync notes"], "PB-109 6: a similar title is suggested (\(sug.map(\.title)))")
    let byPeople = B.suggested([demo, kick], project: "Orbit", titles: ["Budget review"], people: ["Sarah Lee"], peopleOf: { people[$0.key] ?? [] })
    expect(byPeople.map(\.key) == [demo.key], "PB-109 6: a call with the same people is suggested")
    let before = b
    _ = B.suggested(pool, project: "Orbit", titles: ["Weekly sync"], people: [], peopleOf: { _ in [] })
    expect(b == before, "PB-109 6: a suggestion changes nothing until the PM clicks Add")
    expect(B.suggested([other], project: "Orbit", titles: ["Weekly sync"], people: ["Mike Turner"], peopleOf: { _ in ["Mike Turner"] }).isEmpty,
           "PB-109 7: another project's call is never suggested, even with the same title and people")

    // 2. The app's and the skill's transcript of one call are one call in the series
    let skillCopy = B.Call(file: s1.file.replacingOccurrences(of: ".app.md", with: ".md"), title: s1.title, project: "Orbit", start: s1.start)
    expect(b.members(a, in: all + [skillCopy]).filter { $0.key == s1.key }.count == 1, "PB-109 2: one call, one row (app and skill copies)")

    // 8. Moving a call to another project takes it out of its series
    var moved = b
    moved.forget(kick.file)
    let kickMoved = kick.with(project: "Northpoint")
    expect(moved.manual(file: kickMoved.file, title: kickMoved.title, project: "Northpoint") == nil
           && !moved.members(a, in: [s1, s2, kickMoved, demo]).contains { $0.key == kick.key }, "PB-109 8: a moved call leaves its series")
    expect(b.manual(file: kickMoved.file, title: kickMoved.title, project: "Northpoint") == nil, "PB-109 8: even before cleanup another project's call is no member")
}

private extension CallSeriesBook.Call {
    func with(project p: String) -> CallSeriesBook.Call {
        var c = self; c.project = p; c.file = file.replacingOccurrences(of: "/w/" + project + "/", with: "/w/" + p + "/"); return c
    }
}

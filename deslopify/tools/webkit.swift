// Look at a page in WebKit — the engine Safari is built on — the way look.py
// looks at one in Chrome: load it, run an optional probe, report page errors,
// and write a snapshot as WebKit draws it.
//
//     swiftc -O -o /tmp/webkit deslopify/tools/webkit.swift
//     /tmp/webkit URL [--eval FILE.js] [--wait MS] [--shot OUT.png]
//                     [--width W] [--height H]
//
// The probe is the body of an async function; whatever it returns is printed
// as JSON. It may await, e.g. `await new Promise(r => setTimeout(r, 500))`.
//
// Why a compiled program: WKWebView driven through `osascript` never starts
// its content process from the tool environment, but a real app with its own
// window renders normally (2026-09-29). Tancredi reads in Safari, so what
// Chrome's headless shell shows is not the whole answer — see picker.swift
// for the bug that proved it.
import Cocoa
import WebKit

var url: URL?
var evalFile: String?
var waitMs = 1500
var shot: String?
var width = 1280.0
var height = 860.0
var it = CommandLine.arguments.dropFirst().makeIterator()
while let a = it.next() {
    switch a {
    case "--eval": evalFile = it.next()
    case "--wait": waitMs = Int(it.next() ?? "") ?? waitMs
    case "--shot": shot = it.next()
    case "--width": width = Double(it.next() ?? "") ?? width
    case "--height": height = Double(it.next() ?? "") ?? height
    default: url = URL(string: a)
    }
}
if url == nil {
    print("usage: webkit URL [--eval FILE.js] [--wait MS] [--shot OUT.png]")
    exit(2)
}
let target = url!

final class Look: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let page: URL
    init(page: URL) { self.page = page }
    var web: WKWebView!
    var window: NSWindow!
    var errors: [String] = []

    func start() {
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .nonPersistent()
        let ctl = WKUserContentController()
        ctl.add(self, name: "log")
        ctl.addUserScript(WKUserScript(source: """
          window.addEventListener('error', e => window.webkit.messageHandlers.log.postMessage('error: ' + e.message + ' @' + (e.filename || '') + ':' + (e.lineno || '')));
          window.addEventListener('unhandledrejection', e => window.webkit.messageHandlers.log.postMessage('unhandled rejection: ' + (e.reason && (e.reason.stack || e.reason.message) || e.reason)));
          const _e = console.error; console.error = (...a) => { window.webkit.messageHandlers.log.postMessage('console.error: ' + a.join(' ')); _e(...a); };
          """, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        conf.userContentController = ctl
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                          styleMask: [.titled], backing: .buffered, defer: false)
        web = WKWebView(frame: window.contentView!.bounds, configuration: conf)
        web.navigationDelegate = self
        window.contentView!.addSubview(web)
        window.orderBack(nil)
        web.load(URLRequest(url: page))
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        errors.append("\(m.body)")
    }

    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(waitMs)) { self.probe() }
    }

    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) {
        print("navigation failed: \(e)"); exit(1)
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        print("navigation failed: \(e)"); exit(1)
    }

    func probe() {
        guard let file = evalFile, let body = try? String(contentsOfFile: file, encoding: .utf8) else {
            finish(); return
        }
        web.callAsyncJavaScript("return JSON.stringify(await (async () => { \(body) })())",
                                arguments: [:], in: nil, in: .page) { result in
            switch result {
            case .success(let v): print("eval: \(v ?? "null")")
            case .failure(let e): print("eval failed: \(e)")
            }
            self.finish()
        }
    }

    func finish() {
        print("errors: \(errors)")
        guard let out = shot else { exit(0) }
        web.takeSnapshot(with: nil) { img, err in
            if let img = img, let tiff = img.tiffRepresentation,
               let rep = NSBitmapImageRep(data: tiff),
               let png = rep.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: out))
                print("snapshot: \(out)")
            } else {
                print("snapshot failed: \(err.map { "\($0)" } ?? "no image")")
            }
            exit(0)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let look = Look(page: target)
look.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { print("timed out"); exit(3) }
app.run()

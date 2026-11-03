// Click the reader's "choose a PDF" box in WebKit — the engine Safari is built
// on — and play the part of the file dialog: count how often the page asks for
// one, answer with a PDF, and report what the page did with it.
//
//     swiftc -O -o /tmp/picker deslopify/tools/picker.swift
//     /tmp/picker URL PDF [SELECTOR] [MODE] [SNAPSHOT.png]
//
//     SELECTOR   what to click (default #drop)
//     MODE       js    — element.click(), run as a user gesture (default)
//                key   — focus it and press Enter
//                mouse — a synthesised mouse click; does not reach a window in
//                        the background, so it tells you nothing here
//     SNAPSHOT   write a picture of the page afterwards, drawn by WebKit
//
// Why it exists. Chrome's headless shell (look.py) never shows this bug, and
// look.py --upload skips the dialog altogether by setting the input directly.
// On 2026-09-29 the box opened the dialog twice per click — once as the
// input's <label>, once from script. Chrome asks for one dialog either way;
// WebKit shows the first and delivers the chosen file to the second request,
// which replaced it and never appears, so choosing a PDF did nothing in Safari.
// This showed "panels requested: 1" and an empty page before the fix, and the
// choice screen after it.
//
// A successful run uploads the PDF, creating a paper: point it at a test
// server with its own papers folder, or delete the paper afterwards.

import Cocoa
import WebKit

let args = CommandLine.arguments
let url = URL(string: args[1])!
let pdf = URL(fileURLWithPath: args[2])
let selector = args.count > 3 ? args[3] : "#drop"
let mode = args.count > 4 ? args[4] : "js"

final class Probe: NSObject, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var panels = 0
    var web: WKWebView!
    var window: NSWindow!

    func start() {
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .nonPersistent()
        let ctl = WKUserContentController()
        ctl.add(self, name: "log")
        ctl.addUserScript(WKUserScript(source: """
          window.addEventListener('error', e => window.webkit.messageHandlers.log.postMessage('error: ' + e.message));
          const _e = console.error; console.error = (...a) => { window.webkit.messageHandlers.log.postMessage('console.error: ' + a.join(' ')); _e(...a); };
          """, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        conf.userContentController = ctl
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 800),
                          styleMask: [.titled], backing: .buffered, defer: false)
        web = WKWebView(frame: window.contentView!.bounds, configuration: conf)
        web.uiDelegate = self
        web.navigationDelegate = self
        window.contentView!.addSubview(web)
        window.orderBack(nil)
        web.load(URLRequest(url: url))
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        print("page  \(m.body)")
    }

    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.click() }
    }

    func click() {
        if mode == "mouse" {
            let js = "(() => { const r = document.querySelector('\(selector)').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()"
            web.evaluateJavaScript(js) { v, _ in
                guard let xy = v as? [Double] else { print("no element"); exit(2) }
                let p = NSPoint(x: xy[0], y: self.web.bounds.height - xy[1])
                let wp = self.web.convert(p, to: nil)
                for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                    let e = NSEvent.mouseEvent(with: type, location: wp, modifierFlags: [],
                        timestamp: ProcessInfo.processInfo.systemUptime,
                        windowNumber: self.window.windowNumber, context: nil,
                        eventNumber: 0, clickCount: 1, pressure: 1)!
                    self.window.sendEvent(e)
                }
                print("sent a mouse click at \(xy)")
            }
        } else if mode == "key" {
            web.evaluateJavaScript("(() => { const d = document.querySelector('\(selector)'); d.focus(); d.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); return document.activeElement === d ? 'focused, Enter sent' : 'not focused'; })()") { v, err in
                print("key: \(v ?? "nil") \(err.map { "\($0)" } ?? "")")
            }
        } else {
            web.evaluateJavaScript("document.querySelector('\(selector)').click(); 'clicked'") { v, err in
                print("clicked \(selector): \(v ?? "nil") \(err.map { "\($0)" } ?? "")")
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4.0) { self.report() }
    }

    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters,
                 initiatedByFrame f: WKFrameInfo,
                 completionHandler done: @escaping ([URL]?) -> Void) {
        panels += 1
        let n = panels
        print("panel #\(n) requested")
        // A person takes a moment to pick the file.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            print("panel #\(n) answered with \(pdf.lastPathComponent)")
            done([pdf])
        }
    }

    func report() {
        let js = """
          JSON.stringify({
            dropHidden: document.querySelector('#drop')?.hidden ?? null,
            chosen: (document.querySelector('#chosen')?.innerText || '').replace(/\\s+/g, ' ').slice(0, 160),
            files: document.querySelector('#file')?.files?.length ?? null,
          })
          """
        web.evaluateJavaScript(js) { v, _ in
            print("panels requested: \(self.panels)")
            print("page state: \(v ?? "nil")")
            self.web.evaluateJavaScript("document.querySelector('.agent')?.innerText.replace(/\\s+/g, ' ') + ' | widths ' + [...document.querySelectorAll('.agent select')].map(s => s.style.width).join(',')") { a, _ in
                print("agent line: \(a ?? "nil")")
                let shot = CommandLine.arguments.count > 5 ? CommandLine.arguments[5] : ""
                if shot.isEmpty { exit(0) }
                self.web.takeSnapshot(with: nil) { img, err in
                    if let img = img, let tiff = img.tiffRepresentation,
                       let rep = NSBitmapImageRep(data: tiff),
                       let png = rep.representation(using: .png, properties: [:]) {
                        try? png.write(to: URL(fileURLWithPath: shot))
                        print("snapshot written")
                    } else { print("snapshot failed: \(err.map { "\($0)" } ?? "no image")") }
                    exit(0)
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let probe = Probe()
probe.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 20) { print("timed out"); exit(3) }
app.run()

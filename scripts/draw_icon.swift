// Draws the Carry icon at 1024px: swift scripts/draw_icon.swift out.png
// Then: sips resizes into Carry.iconset, iconutil -c icns builds src/carry/app/Carry.icns.
import AppKit
let S: CGFloat = 1024
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0, space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let path = CGPath(roundedRect: body, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 24, color: CGColor(gray: 0, alpha: 0.28))
ctx.addPath(path); ctx.setFillColor(CGColor(srgbRed: 0.24, green: 0.29, blue: 0.80, alpha: 1)); ctx.fillPath()
ctx.restoreGState()
ctx.saveGState(); ctx.addPath(path); ctx.clip()
let g = CGGradient(colorsSpace: cs, colors: [CGColor(srgbRed: 0.20, green: 0.24, blue: 0.66, alpha: 1), CGColor(srgbRed: 0.29, green: 0.35, blue: 0.93, alpha: 1)] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(g, start: CGPoint(x: 100, y: 924), end: CGPoint(x: 924, y: 100), options: [])
ctx.restoreGState()
// The big C, same size as before, moved left to make room for "arry"
let cx: CGFloat = 425
ctx.setStrokeColor(CGColor(gray: 1, alpha: 1)); ctx.setLineWidth(128); ctx.setLineCap(.round)
let open: CGFloat = .pi / 4.2
ctx.addArc(center: CGPoint(x: cx, y: 512), radius: 225, startAngle: open, endAngle: -open, clockwise: false)
ctx.strokePath()
// "arry" small, running on from the C's lower end
let size: CGFloat = 112
var font = NSFont.systemFont(ofSize: size, weight: .heavy)
if let d = font.fontDescriptor.withDesign(.rounded) { font = NSFont(descriptor: d, size: size)! }
let text = NSAttributedString(string: "arry", attributes: [.font: font, .foregroundColor: NSColor.white])
NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
let w = text.size().width
let x = cx + 240
let baseline: CGFloat = 327  // level with the C's lower end
text.draw(at: CGPoint(x: x, y: baseline + font.descender))
let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon-1024.png"))

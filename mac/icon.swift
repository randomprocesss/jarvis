// Renders Jarvis's 1024px icon: a deep navy rounded square with a luminous arc-blue ring reactor and a soft glow.
// Usage: swift icon.swift out.png
import AppKit
import CoreGraphics

let S: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!

func rgb(_ h: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((h >> 16) & 255) / 255, green: CGFloat((h >> 8) & 255) / 255, blue: CGFloat(h & 255) / 255, alpha: a)
}
let c = CGPoint(x: S / 2, y: S / 2)

// Rounded square (Apple grid: 824px body inside 1024 with ~185px corner radius).
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let shape = CGPath(roundedRect: body, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: rgb(0x000000, 0.45))
ctx.addPath(shape); ctx.setFillColor(rgb(0x020c16)); ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(shape); ctx.clip()
// Ground: navy with a slightly lifted centre.
let ground = CGGradient(colorsSpace: cs, colors: [rgb(0x0a2a3c), rgb(0x04121e), rgb(0x01070d)] as CFArray, locations: [0, 0.55, 1])!
ctx.drawRadialGradient(ground, startCenter: c, startRadius: 0, endCenter: c, endRadius: 560, options: [.drawsAfterEndLocation])
// Soft glow.
let glow = CGGradient(colorsSpace: cs, colors: [rgb(0x3fc8ff, 0.55), rgb(0x3fc8ff, 0.12), rgb(0x3fc8ff, 0)] as CFArray, locations: [0, 0.45, 1])!
ctx.drawRadialGradient(glow, startCenter: c, startRadius: 0, endCenter: c, endRadius: 360, options: [])

func ring(_ r: CGFloat, _ w: CGFloat, _ col: CGColor, blur: CGFloat = 0) {
    ctx.saveGState()
    if blur > 0 { ctx.setShadow(offset: .zero, blur: blur, color: rgb(0x3fc8ff, 0.9)) }
    ctx.setStrokeColor(col); ctx.setLineWidth(w)
    ctx.addEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)); ctx.strokePath()
    ctx.restoreGState()
}
// Hairline outer ring, main luminous ring, inner ring.
ring(300, 2, rgb(0x9fe7ff, 0.28))
ring(262, 16, rgb(0x6fd8ff, 0.95), blur: 40)
ring(262, 5, rgb(0xe6f9ff, 0.9))
ring(205, 2.5, rgb(0x9fe7ff, 0.45))

// Ten segments between rings, like reactor coils.
ctx.saveGState()
ctx.setShadow(offset: .zero, blur: 18, color: rgb(0x3fc8ff, 0.8))
for i in 0..<10 {
    let a0 = CGFloat(i) * .pi / 5 + 0.07, a1 = a0 + .pi / 5 - 0.14
    let p = CGMutablePath()
    p.addArc(center: c, radius: 240, startAngle: a0, endAngle: a1, clockwise: false)
    p.addArc(center: c, radius: 218, startAngle: a1, endAngle: a0, clockwise: true)
    p.closeSubpath()
    ctx.addPath(p); ctx.setFillColor(rgb(0x3fc8ff, 0.55)); ctx.fillPath()
}
ctx.restoreGState()

// Core: bright centre fading to arc blue.
let core = CGGradient(colorsSpace: cs, colors: [rgb(0xffffff), rgb(0x9fe7ff), rgb(0x3fc8ff, 0.85), rgb(0x3fc8ff, 0)] as CFArray,
                      locations: [0, 0.3, 0.62, 1])!
ctx.drawRadialGradient(core, startCenter: c, startRadius: 0, endCenter: c, endRadius: 150, options: [])
ring(118, 3, rgb(0xd8f6ff, 0.75))

// The one tiny gold detail: a small dot on the outer hairline.
let g = CGPoint(x: c.x + 300 * cos(.pi * 0.25), y: c.y + 300 * sin(.pi * 0.25))
ctx.setShadow(offset: .zero, blur: 10, color: rgb(0xe8b04b, 0.8))
ctx.setFillColor(rgb(0xe8b04b)); ctx.fillEllipse(in: CGRect(x: g.x - 9, y: g.y - 9, width: 18, height: 18))
ctx.restoreGState()

// Top-lit 1px edge.
ctx.addPath(shape); ctx.setStrokeColor(rgb(0x9fe7ff, 0.16)); ctx.setLineWidth(3); ctx.strokePath()

let img = ctx.makeImage()!
let rep = NSBitmapImageRep(cgImage: img)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")

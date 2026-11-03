// Renders the launcher icon (timeline bars) to a 1024px PNG.
// Usage: swift make-icon.swift out.png [cold]   ("cold" adds a snowflake, bottom-right)
import AppKit

let size: CGFloat = 1024
let scale = size / 104
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1024, pixelsHigh: 1024, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

func color(_ hex: Int) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}
// Coordinates are in the 104-unit design grid with a top-left origin.
func rect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat, _ c: NSColor) {
    c.setFill()
    NSBezierPath(roundedRect: NSRect(x: x * scale, y: size - (y + h) * scale, width: w * scale, height: h * scale),
                 xRadius: r * scale, yRadius: r * scale).fill()
}

rect(0, 0, 104, 104, 24, color(0x111827))
rect(20, 24, 40, 12, 6, color(0x38BDF8))
rect(34, 46, 52, 12, 6, color(0xA78BFA))
rect(20, 68, 34, 12, 6, color(0x34D399))
rect(60, 68, 24, 12, 6, color(0xFBBF24))

if CommandLine.arguments.count > 2 && CommandLine.arguments[2] == "cold" {
    // Snowflake badge, bottom-right: a dark disc (so it reads over the bars) and three crossing branches.
    let cx: CGFloat = 80, cy: CGFloat = 80
    color(0x111827).setFill()
    NSBezierPath(ovalIn: NSRect(x: (cx - 19) * scale, y: size - (cy + 19) * scale, width: 38 * scale, height: 38 * scale)).fill()
    func pt(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: x * scale, y: size - y * scale) }
    NSColor.white.setStroke()
    let flake = NSBezierPath()
    flake.lineWidth = 3 * scale
    flake.lineCapStyle = .round
    let r: CGFloat = 14
    for i in 0..<3 {
        let a = CGFloat(i) * .pi / 3
        for sign in [CGFloat(1), -1] {
            // Local frame: u along the branch, v perpendicular.
            func p(_ u: CGFloat, _ v: CGFloat) -> NSPoint {
                let ux = sign * cos(a), uy = sign * sin(a)
                return pt(cx + u * ux - v * uy, cy + u * uy + v * ux)
            }
            flake.move(to: p(0, 0)); flake.line(to: p(r, 0))
            flake.move(to: p(r * 0.62, 0)); flake.line(to: p(r * 0.88, 4.5))
            flake.move(to: p(r * 0.62, 0)); flake.line(to: p(r * 0.88, -4.5))
        }
    }
    flake.stroke()
}

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))

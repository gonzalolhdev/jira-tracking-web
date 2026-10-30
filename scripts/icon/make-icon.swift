// Renders the launcher icon (timeline bars) to a 1024px PNG.
// Usage: swift make-icon.swift out.png
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

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))

import type { VehicleKind } from '../../api/types'

// Top-down vehicle glyphs, drawn pointing north (MapLibre rotates them by heading).
const PIXEL_RATIO = 2

interface Spec {
  length: number
  width: number
  body: string
  roof: string
}

const SPECS: Record<VehicleKind, Spec> = {
  car: { length: 11, width: 5.5, body: '#c5d0de', roof: '#8a97a8' },
  truck: { length: 18, width: 6, body: '#9aa8b8', roof: '#6d7a8a' },
  bus: { length: 22, width: 6.5, body: '#6fb2ec', roof: '#3f7fb8' },
  emergency: { length: 14, width: 6.5, body: '#ffffff', roof: '#ff3b3b' },
  disabled: { length: 11, width: 5.5, body: '#ff8a3d', roof: '#b44d12' },
}

function draw(spec: Spec): { width: number; height: number; data: Uint8Array } {
  const pad = 2
  const w = Math.ceil((spec.width + pad * 2) * PIXEL_RATIO)
  const h = Math.ceil((spec.length + pad * 2) * PIXEL_RATIO)
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')!
  ctx.scale(PIXEL_RATIO, PIXEL_RATIO)
  const x = pad
  const y = pad
  // dark outline keeps light vehicles legible on the road surface
  ctx.fillStyle = 'rgba(5, 8, 12, 0.9)'
  ctx.beginPath()
  ctx.roundRect(x - 0.75, y - 0.75, spec.width + 1.5, spec.length + 1.5, 2)
  ctx.fill()
  ctx.fillStyle = spec.body
  ctx.beginPath()
  ctx.roundRect(x, y, spec.width, spec.length, 1.6)
  ctx.fill()
  // roof / cab marks the front
  ctx.fillStyle = spec.roof
  ctx.fillRect(x + 1, y + spec.length * 0.22, spec.width - 2, spec.length * 0.3)
  const data = ctx.getImageData(0, 0, w, h).data
  return { width: w, height: h, data: new Uint8Array(data.buffer) }
}

export function vehicleIconImages(): { id: string; image: ReturnType<typeof draw> }[] {
  return (Object.keys(SPECS) as VehicleKind[]).map((kind) => ({ id: `veh-${kind}`, image: draw(SPECS[kind]) }))
}

export const VEHICLE_ICON_PIXEL_RATIO = PIXEL_RATIO

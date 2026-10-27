declare module '@novnc/novnc' {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, urlOrChannel: string | object, options?: { shared?: boolean; credentials?: object })
    viewOnly: boolean
    scaleViewport: boolean
    background: string
    resizeSession: boolean
    focusOnClick: boolean
    sendKey(keysym: number, code: string | null, down?: boolean): void
    focus(options?: FocusOptions): void
    disconnect(): void
  }
}

import './app.css'
import { mount } from 'svelte'
import Admin from './admin/Admin.svelte'

const root = document.getElementById('root')!
mount(Admin, {
  target: root,
  props: { mount: root.dataset.mount ?? '', console: root.dataset.console ?? '/' },
})

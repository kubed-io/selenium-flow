import './app.css'
import { mount } from 'svelte'
import App from './App.svelte'

const root = document.getElementById('root')!
root.textContent = ''
mount(App, { target: root })

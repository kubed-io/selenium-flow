import { render } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import FileSections from './FileSections.svelte'

test('three titled rows with counts, in order, read-only (P1)', () => {
  const { container } = render(FileSections, { data: { downloads: [], screenshots: [{ name: 's.png', size: 1, url: '/s', image: true }], files: [] } })
  const rows = [...container.querySelectorAll('section.row')]
  expect(rows.map((r) => r.querySelector('strong')!.textContent)).toEqual(['Downloads', 'Screenshots', 'Files'])
  expect(rows.map((r) => r.querySelector('.pill')!.textContent)).toEqual(['0', '1', '0'])
  expect(rows[2]).toHaveTextContent('Nothing here yet — prints land here, and anything you keep.')
  expect(container.querySelector('button')).toBeNull()
})

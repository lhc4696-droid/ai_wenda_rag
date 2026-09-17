<script setup>
defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
})
const paths = {
  chat: [
    'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-8l-6 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
    'M8 9h8M8 13h5',
  ],
  plus: ['M12 5v14M5 12h14'],
  arrow: ['M12 19V5M6 11l6-6 6 6'],
  chevron: ['m9 5 7 7-7 7'],
  'chevron-down': ['m6 9 6 6 6-6'],
  user: ['M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z', 'M4 21v-2a8 8 0 0 1 16 0v2'],
  lock: ['M6 10h12a1 1 0 0 1 1 1v9H5v-9a1 1 0 0 1 1-1Z', 'M8 10V7a4 4 0 0 1 8 0v3M12 14v3'],
  logout: ['M9 4H4v16h5M9 12h12m-5-5 5 5-5 5'],
  trash: ['M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'],
  spark: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z'],
  book: ['M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14'],
  pen: ['m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14l-1 7Z'],
  layers: ['m12 3 10 5-10 5L2 8Zm-9 10 9 5 9-5M3 18l9 5 9-5'],
  copy: ['M9 9h11v12H9z', 'M15 9V3H3v12h6'],
  check: ['m5 12 4 4L19 6'],
  refresh: ['M20 7v5h-5M4 17v-5h5', 'M6 6a8 8 0 0 1 13 2M5 16a8 8 0 0 0 13 2'],
  menu: ['M4 6h16M4 12h16M4 18h16'],
  close: ['m6 6 12 12M6 18 18 6'],
}
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(path, index) in paths[name] || paths.chat" :key="index" :d="path" />
  </svg>
</template>

/** @type {import('tailwindcss').Config} */
// The demo styles with Tailwind's built-in palette. A custom `aws` palette was
// declared here and referenced only by component classes in index.css that
// nothing used, so both are gone rather than left as dead configuration.
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
};

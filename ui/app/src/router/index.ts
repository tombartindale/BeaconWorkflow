import { defineRouter } from '#q-app';
import { createRouter, createWebHashHistory } from 'vue-router';
import routes from './routes';

// Hash history, so every #/… link and bookmark from the previous UI keeps working.
export default defineRouter(() => createRouter({
  scrollBehavior: () => ({ left: 0, top: 0 }),
  routes,
  history: createWebHashHistory(import.meta.env.QUASAR_VUE_ROUTER_BASE),
}));

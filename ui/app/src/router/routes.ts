import type { RouteLocationNormalized, RouteRecordRaw } from 'vue-router';

const TOPIC = '[A-Z]{2}\\d{4}-U\\d{2}-T\\d{2}';
const MODULE = '[A-Z]{2}\\d{4}';
const num = (v: unknown) => (v === undefined || v === null || v === '' || Array.isArray(v) ? null : Number(v));
type R = RouteLocationNormalized;

declare module 'vue-router' {
  interface RouteMeta { nav?: string }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      { path: '', component: () => import('@/pages/ProgrammePage.vue'), meta: { nav: 'programme' } },
      { path: `module/:module(${MODULE})`, component: () => import('@/pages/ModulePage.vue'), props: true, meta: { nav: 'programme' } },
      { path: `topic/:id(${TOPIC})`, component: () => import('@/pages/TopicPage.vue'), meta: { nav: 'programme' },
        props: (r: R) => ({ id: r.params.id, startAt: num(r.query.t) }) },
      { path: `edit/:id(${TOPIC})`, component: () => import('@/pages/EditorPage.vue'), meta: { nav: 'programme' },
        props: (r: R) => ({ id: r.params.id, lang: r.query.lang === 'zh' ? 'zh' : 'en', startLine: num(r.query.line) }) },
      { path: `doc/:path(${MODULE}/.+)`, component: () => import('@/pages/DocPage.vue'), meta: { nav: 'programme' },
        props: (r: R) => ({ path: r.params.path, line: num(r.query.line) }) },
      { path: 'diagnostics/:tab(mistranscriptions)?', component: () => import('@/pages/DiagnosticsPage.vue'), meta: { nav: 'diagnostics' },
        props: (r: R) => ({ tab: r.params.tab || 'codes' }) },
      { path: 'jobs/:open(\\d+)?', component: () => import('@/pages/JobsPage.vue'), meta: { nav: 'jobs' },
        props: (r: R) => ({ openId: num(r.params.open) }) },
      { path: 'intake', component: () => import('@/pages/IntakePage.vue'), meta: { nav: 'intake' } },
      { path: 'translation', component: () => import('@/pages/TranslationPage.vue'), meta: { nav: 'translation' } },
      { path: 'sync', component: () => import('@/pages/SyncPage.vue'), meta: { nav: 'sync' } },
      { path: 'settings', component: () => import('@/pages/SettingsPage.vue'), meta: { nav: 'settings' } },
    ],
  },
  // The teleprompter is full screen: no header or navigation.
  { path: `/prompt/:id(${TOPIC})`, component: () => import('@/pages/PrompterPage.vue'), props: true },
  { path: '/:rest(.*)*', redirect: '/' },
];

export default routes;

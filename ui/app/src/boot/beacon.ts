// Before the app mounts: learn about the programme, then keep up with it live.
import { defineBoot } from '#q-app';
import { useBeacon } from '@/stores/beacon';

export default defineBoot(async ({ store }) => {
  const beacon = useBeacon(store);
  await beacon.loadBoot();
  if (!beacon.boot) return;
  beacon.connect();
  await Promise.allSettled([beacon.loadStatus(), beacon.loadJobs()]);
});

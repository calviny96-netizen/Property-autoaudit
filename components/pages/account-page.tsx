'use client';

import { PageHeader } from '@/components/app/primitives';
import { AccountTab } from '@/components/settings/account-tab';

/** Platform admin: the admin's own account (companies reach the same tab under Settings). */
export default function AccountPage() {
  return (
    <>
      <PageHeader title="My account" description="Who you are signed in as, and your password." />
      <AccountTab />
    </>
  );
}

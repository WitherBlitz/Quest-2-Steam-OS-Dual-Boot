/* SPDX-License-Identifier: GPL-2.0-only */
/* Shared controlled-clock cases. Caller supplies QOG_FAILURE and lock doubles
 * or real kernel locks. State belongs exclusively to this test instance.
 */
#define QOG_EXPECT(expr) do { if (!(expr)) { QOG_FAILURE(__LINE__); return -EINVAL; } } while (0)
static void qog_reset(void)
{
	memset(qkx_owner_states, 0, sizeof(qkx_owner_states));
	qkx_owner_states[0].epoch = qkx_owner_states[1].epoch = 1;
	clock_now = 1000;
	clock_on_lock = 0;
}
static int qog_cases(void)
{
	struct qkx_owner_token a = {0}, b = {0};
	struct qkx_owner_fence f, old;
	struct qkx_owner_view v;
	unsigned int cases = 0;
	qog_reset(); QOG_EXPECT(!locks);
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && !v.admission_closed && v.epoch==1); cases++;
	QOG_EXPECT(!qkx_owner_enter(0,QKX_OWNER_SUBMIT,&a));
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && v.active_submit==1);
	qkx_owner_leave(&a); QOG_EXPECT(!a.counted); cases++;
	QOG_EXPECT(!qkx_owner_enter(0,QKX_OWNER_SUBMIT,&a));
	QOG_EXPECT(qkx_owner_try_fence(0,10,&f)==-EBUSY && !f.id); qkx_owner_leave(&a); cases++;
	QOG_EXPECT(!qkx_owner_try_fence(0,10,&f));
	QOG_EXPECT(qkx_owner_enter(0,QKX_OWNER_SUBMIT,&a)==-EBUSY && !a.counted);
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && v.entry_fence_stable); cases++;
	QOG_EXPECT(!qkx_owner_enter(0,QKX_OWNER_LIFECYCLE,&a));
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && !v.entry_fence_stable && v.admission_closed);
	qkx_owner_leave(&a); QOG_EXPECT(!qkx_owner_reopen(&f)); cases++;
	QOG_EXPECT(!qkx_owner_try_fence(0,10,&f)); clock_now+=10;
	QOG_EXPECT(!qkx_owner_enter(0,QKX_OWNER_SUBMIT,&a)); qkx_owner_leave(&a);
	QOG_EXPECT(qkx_owner_reopen(&f)==-ESTALE); cases++;
	QOG_EXPECT(!qkx_owner_try_fence(0,10,&old)); QOG_EXPECT(!qkx_owner_reopen(&old));
	QOG_EXPECT(!qkx_owner_try_fence(0,10,&f)); QOG_EXPECT(qkx_owner_reopen(&old)==-ESTALE);
	QOG_EXPECT(!qkx_owner_reopen(&f)); cases++;
	qog_reset(); qkx_owner_states[0].active[0]=~(u32)0;
	QOG_EXPECT(qkx_owner_enter(0,QKX_OWNER_SUBMIT,&a)==-EOVERFLOW);
	QOG_EXPECT(!qkx_owner_enter(0,QKX_OWNER_LIFECYCLE,&a) && !a.counted);
	QOG_EXPECT(qkx_owner_try_fence(0,1,&f)==-EOVERFLOW); cases++;
	qog_reset(); QOG_EXPECT(!qkx_owner_snapshot(0,&v)); clock_now--;
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && v.poisoned); cases++;
	qog_reset(); QOG_EXPECT(qkx_owner_enter(2,0,&a)==-EINVAL);
	QOG_EXPECT(qkx_owner_try_fence(0,0,&f)==-EINVAL);
	QOG_EXPECT(qkx_owner_try_fence(0,QKX_OWNER_MAX_FENCE_NS+1,&f)==-EINVAL);
	QOG_EXPECT(qkx_owner_snapshot(0,NULL)==-EINVAL); cases++;
	QOG_EXPECT(!qkx_owner_try_fence(0,10,&f));
	QOG_EXPECT(!qkx_owner_enter(1,QKX_OWNER_SUBMIT,&b)); qkx_owner_leave(&b);
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && v.entry_fence_stable); cases++;
	QOG_EXPECT(!qkx_owner_reopen(&f)); QOG_EXPECT(!qkx_owner_enter(0,0,&a));
	QOG_EXPECT(qkx_owner_enter(0,0,&a)==-EINVAL); qkx_owner_leave(&a); qkx_owner_leave(&a);
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && !v.active_submit && !v.poisoned); cases++;
	qog_reset(); QOG_EXPECT(!qkx_owner_snapshot(0,&v)); clock_now=900; clock_on_lock=1100;
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && !v.poisoned); cases++;
	qog_reset(); clock_now=~(u64)0-1;
	QOG_EXPECT(qkx_owner_try_fence(0,10,&f)==-EOVERFLOW); cases++;
	qog_reset(); qkx_owner_states[0].epoch=~(u64)0-1;
	QOG_EXPECT(!qkx_owner_enter(0,0,&a)); qkx_owner_leave(&a);
	QOG_EXPECT(!qkx_owner_snapshot(0,&v) && v.poisoned && !v.admission_closed); cases++;
	QOG_EXPECT(cases==15 && !locks);
	return cases;
}
#undef QOG_EXPECT

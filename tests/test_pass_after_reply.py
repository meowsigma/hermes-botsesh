"""Parent add: a confirmed authored post is not a failed or quiet turn."""
import copy
import unittest
import test_reply_argument_slip as fixture

class PassAfterReplyTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the real registry/deferred-validator/executor fixture, not its tests.
    asyncSetUp=fixture.ReplyArgumentSlipTests.asyncSetUp
    prepare=fixture.ReplyArgumentSlipTests.prepare
    begin=fixture.ReplyArgumentSlipTests.begin
    core_call=fixture.ReplyArgumentSlipTests.core_call

    async def test_processed_reply_then_pass_is_an_already_replied_noop(self):
        adapter,args=await self.prepare()
        posted,_,_=self.core_call({**args,'text':'A confirmed answer.','reply_to':3},name='botsesh_post')
        self.assertTrue(posted['ok'],posted)
        before=copy.deepcopy(self.client.store.room('a')['processing_receipts']['3'])
        count=len(self.requests)
        # No-op is not permission to accept a wrong receipt or unread assertion.
        refused,agent,_=self.core_call({**args,'receipt_id':'foreign'})
        self.assertFalse(refused['ok']);agent.interrupt.assert_not_called()
        refused,agent,_=fixture.core_fixture.ConfirmedPassTests.core_call(self,{**args,'processed':False})
        self.assertFalse(refused['ok']);agent.interrupt.assert_not_called()
        result,agent,_=self.core_call(args)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['status'],'already_replied')
        self.assertIn('Already replied',result['result']['message'])
        self.assertEqual(result['result']['read_effect'],'none')
        self.assertEqual(len(self.requests),count,'no-op has no RPC effects')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3'],before)
        agent.interrupt.assert_not_called()
        self.assertIn('a',adapter._current,'ordinary authored posts remain nonterminal')
        adapter._current['a']['model_processed']=True
        await adapter._finish_current('a',adapter._current['a'],'empty_response')
        row=self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'],'replied');self.assertNotIn('explicit_pass',row)
        self.assertFalse(any(n=='finish_room_turn' for _,n,_ in self.requests))
        self.assertEqual(len(self.messages),1)

    async def test_unprocessed_reply_then_pass_does_not_ack_or_manufacture_a_quiet_pass(self):
        adapter,args=await self.prepare()
        posted,_,_=self.core_call({'room_id':'a','text':'An authored no-read message.'},name='botsesh_post')
        self.assertTrue(posted['ok'],posted)
        self.assertEqual(posted['result']['read_effect'],'none')
        count=len(self.requests)
        result,agent,_=self.core_call(args)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['status'],'already_replied')
        self.assertEqual(result['result']['read_effect'],'none')
        self.assertEqual(len(self.requests),count);agent.interrupt.assert_not_called()
        row=self.client.store.room('a')['processing_receipts']['3']
        self.assertNotIn('read_attested',row);self.assertNotIn('explicit_pass',row)
        adapter._current['a']['model_processed']=True
        await adapter._finish_current('a',adapter._current['a'],'empty_response')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'],'completed_unread')
        self.assertFalse(any(n=='finish_room_turn' for _,n,_ in self.requests))

    async def test_confirmed_recovery_after_rejection_can_report_already_replied(self):
        _,args=await self.prepare()
        failed,_,_=self.core_call({'room_id':'a','text':'Rejected unknown receipt.','processed':True,'receipt_id':'unknown-receipt'},name='botsesh_post')
        self.assertFalse(failed['ok'])
        posted,_,_=self.core_call({**args,'text':'The corrected confirmed reply.','reply_to':3},name='botsesh_post')
        self.assertTrue(posted['ok'],posted)
        result,agent,_=self.core_call(args)
        self.assertTrue(result['ok'],result);self.assertEqual(result['result']['status'],'already_replied')
        agent.interrupt.assert_not_called()
        self.assertNotIn('explicit_pass',self.client.store.room('a')['processing_receipts']['3'])

if __name__=='__main__':unittest.main()

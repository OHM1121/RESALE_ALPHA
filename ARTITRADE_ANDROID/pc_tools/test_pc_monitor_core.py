import unittest
from artitrade_pc_monitor_core import (
    validate_hub_url, ReadOnlyHubClient, normalize_frame,
    extract_segments, summarize_research, flatten_items,
)

class MonitorCoreTests(unittest.TestCase):
    def test_rejects_public_and_unencrypted_networks(self):
        for url in ('http://192.168.0.1:8765','https://100.99.1.2:8765',
                    'http://8.8.8.8:8765','http://example.com:8765',
                    'http://100.99.1.2:8765/bad'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_hub_url(url)
        self.assertEqual(validate_hub_url('http://100.99.1.2:8765'),
                         'http://100.99.1.2:8765')

    def test_client_uses_only_get_and_unchanged_revisions_are_reused(self):
        called=[]
        remote={'/v1/status':{'ok':True,'stock':{'desired':'phone'},'coin':{'desired':'phone'}}}
        specs=[
            ('stock','at'),('stock','cw'),('stock','shared'),
            ('coin','general'),('coin','ma'),('coin','shared')]
        for market,strategy in specs:
            for bucket in (('ledger',) if strategy=='shared' else ('ledger','research','errors')):
                key=(market,strategy,bucket)
                remote[('/v1/record/meta',key)]={'ok':True,'exists':True,'revision':1,'updated_at':100000}
                remote[('/v1/record',key)]={'ok':True,'exists':True,'revision':1,'data':{'items':[],'positions':[]},'updated_at':100000}
        def requester(method,path,params):
            called.append((method,path,params))
            if params: return remote[(path,(params['market'],params['strategy'],params['bucket']))]
            return remote[path]
        client=ReadOnlyHubClient('http://100.99.1.2:8765','f'*64,requester=requester)
        first=client.fetch()
        self.assertTrue(first['connected'])
        self.assertEqual(len([x for x in called if x[1]=='/v1/record']),14)
        client.fetch()
        self.assertEqual(len([x for x in called if x[1]=='/v1/record']),14)
        self.assertTrue(all(x[0]=='GET' for x in called))

    def test_nested_stock_ma_gmo_and_coin_remain_distinct(self):
        raw={
            ('stock','at','ledger'):{'positions':[{'code':'AT'}],
                 'ma_subledger':{'positions':[{'code':'MA'}]},
                 'gmo':{'positions':[{'code':'GMO'}]}},
            ('stock','at','research'):{'items':[{'score':50,'bought':True}],
                 'ma':{'items':[{'score':70,'bought':True}]},
                 'gmo':{'items':[{'score':90,'bought':False}]}},
            ('stock','cw','ledger'):{'positions':[{'code':'CW'}]},
            ('coin','general','research'):{'items':[{'market':'KRW-BTC','score':60}]},
            ('coin','ma','research'):{'items':[{'market':'KRW-ETH','score':80}]},
        }
        segments=extract_segments(raw)
        self.assertEqual(segments['stock/ma']['ledger']['positions'][0]['code'],'MA')
        self.assertEqual(segments['stock/gmo']['ledger']['positions'][0]['code'],'GMO')
        self.assertEqual(segments['stock/close']['ledger']['positions'][0]['code'],'CW')
        self.assertEqual(segments['coin/general']['research']['items'][0]['market'],'KRW-BTC')
        self.assertEqual(segments['coin/ma']['research']['items'][0]['market'],'KRW-ETH')

    def test_research_per_score_and_timeframe_keeps_actual_virtual_separate(self):
        items=[
            {'tf':'5m','score':50,'bought':True,'outcome':'HIT'},
            {'tf':'5m','score':60,'bought':True,'outcome':'MISS'},
            {'tf':'5m','score':60,'bought':False,'entry_observed':True,'outcome':'HIT'},
            {'tf':'5m','score':60,'bought':False,'entry_observed':True,'outcome':'PENDING'},
            {'tf':'5m','score':60,'bought':False,'entry_observed':False,'outcome':'HIT'},
            {'tf':'30m','score':70,'bought':True,'outcome':'HIT'},
        ]
        groups=summarize_research(items)
        row=next(x for x in groups if x['frame']=='5m' and x['band']=='41~60점')
        self.assertEqual(row['signals'],5)
        self.assertEqual((row['actual_count'],row['actual_hit'],row['actual_miss'],row['actual_rate']),
                         (2,1,1,50.0))
        self.assertEqual((row['virtual_entries'],row['virtual_hit'],row['virtual_pending'],row['virtual_rate']),
                         (2,1,1,100.0))
        self.assertEqual(normalize_frame('1h'),'60m')
        self.assertEqual(normalize_frame('1d'),'day')

    def test_flatten_items_keeps_strategy_labels(self):
        parts={'stock/gmo':{'ledger':{'positions':[{'code':'A'}]},'research':{'items':[]}},
               'coin/ma':{'ledger':{'positions':[{'market':'KRW-BTC','tf':'30m'}]},'research':{'items':[]}}}
        rows=flatten_items(parts,'positions')
        self.assertEqual({x['_source'] for x in rows},{'stock/gmo','coin/ma'})

    def test_absent_remote_record_is_not_reported_as_valid_latest(self):
        def requester(method,path,params):
            if path=='/v1/status': return {'ok':True}
            return {'ok':True,'exists':False,'revision':0}
        client=ReadOnlyHubClient('http://100.99.1.2:8765','a'*64,requester=requester)
        snapshot=client.fetch()
        self.assertEqual(snapshot['records'],{})
        self.assertEqual(len(snapshot['metas']),14)

if __name__=='__main__':
    unittest.main()

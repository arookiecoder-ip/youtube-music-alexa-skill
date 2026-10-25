import unittest
from home_feed import assemble_home_feed


def track(index):
    return {'videoId': f'{index:011d}', 'title': f'Song {index}', 'artists': [{'name':'Artist'}]}


class HomeShelfVarietyTests(unittest.TestCase):
    def test_native_shelves_beyond_twelve_and_single_item_shelves_remain_visible(self):
        native = [{'title': f'Shelf {i}', 'contents':[track(i)]} for i in range(40)]
        result = assemble_home_feed({'ytm_home':native})
        self.assertEqual(len(result['shelves']),40)
        self.assertTrue(all(len(s['items'])==1 for s in result['shelves']))

    def test_same_heading_with_different_recommendations_is_not_dropped(self):
        native = [{'title':'For you','contents':[track(i)]} for i in (1,2,1)]
        result = assemble_home_feed({'ytm_home':native})
        self.assertEqual(len(result['shelves']),2)
        self.assertNotEqual(result['shelves'][0]['id'],result['shelves'][1]['id'])

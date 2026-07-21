import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_catalog import SourceCatalog, resolve_localisation

ROOT = Path(__file__).resolve().parents[1]


class TechnologyFidelityTests(unittest.TestCase):
    def test_official_ui_has_experimental_notice_without_tester_branding(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('Experimental', html)
        self.assertIn('Imported technologies are read-only. Review generated effects before export.', html)
        self.assertNotIn('ISOLATED TECHNOLOGY TREE TESTER', html)

    def test_recursive_localisation_substitution_and_cycle_protection(self):
        values = {'outer': 'Improved $inner$', 'inner': 'Aircraft Engine', 'cycle_a': '$cycle_b$', 'cycle_b': '$cycle_a$'}
        self.assertEqual(resolve_localisation(values['outer'], values, {'outer'}), ('Improved Aircraft Engine', False))
        resolved, warning = resolve_localisation(values['cycle_a'], values, {'cycle_a'})
        self.assertEqual(resolved, '')
        self.assertTrue(warning)

    def test_inherited_bba_icon_is_used_when_r56_override_has_no_icon(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = SourceCatalog(Path(temp) / 'catalog.sqlite3')
            with catalog.connect() as db:
                for sid, name, layer, order in (('vanilla','Vanilla','vanilla',0),('r56','The Road to 56','dependency',13)):
                    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,1)',(sid,name,layer,order,'fixture','hash','{}','{}'))
                base={'folder':'bba_air_techs_folder','position':{'x':1,'y':2},'year':1936,'researchCost':1,'layoutSource':'interface/Technologies.gfx','icon':'GFX_technology_dummy','iconResolved':True,'iconUrl':'/api/source-icon?source=vanilla&name=dummy.dds','interfaceFile':'interface/Technologies.gfx','prerequisites':[]}
                override=base|{'iconResolved':False,'iconUrl':'','interfaceFile':''}
                for sid,norm in (('vanilla',base),('r56',override)):
                    db.execute('INSERT INTO entities(entity_type,entity_id,display_name,source_id,source_file,source_line,raw_text,normalized,requirements) VALUES(?,?,?,?,?,?,?,?,?)',('technology','dummy','Dummy',sid,'common/technologies/bba.txt',1,'research_cost=1',json.dumps(norm),'{}'))
            item=catalog.technology_tree('road_to_56','GER','bba_air_techs_folder')['items'][0]
            self.assertTrue(item['normalized']['iconResolved'])
            self.assertEqual(item['normalized']['iconResolution'],'inherited source sprite')
            self.assertEqual(item['normalized']['iconInheritedFrom']['source'],'Vanilla')

    def test_source_layout_precedes_fallback_and_category_strategy_is_reported(self):
        items=[{'entity_id':'basic_small_airframe','source_file':'common/technologies/bba.txt','normalized':{'position':{'x':0,'y':4},'layoutSource':'interface/Technologies.gfx','year':1936,'prerequisites':[]}}, {'entity_id':'missing','source_file':'dummy','normalized':{'year':1936,'prerequisites':[]}}]
        script="const L=require(process.argv[1]),r=L.layout(JSON.parse(process.argv[2]),{category:'bba_air_techs_folder'});console.log(JSON.stringify({p:[...r.positions.values()],m:r.metrics}))"
        result=subprocess.run(['node','-e',script,str(ROOT/'technology_layout.js'),json.dumps(items)],capture_output=True,text=True,check=True)
        data=json.loads(result.stdout)
        self.assertEqual(data['p'][0]['placement'],'source-category')
        self.assertEqual(data['p'][1]['placement'],'fallback')
        self.assertEqual(data['m']['strategy'],'category-specific')

    def test_connection_highlighting_and_game_modder_separation_are_wired(self):
        app=(ROOT/'app.js').read_text(encoding='utf-8'); html=(ROOT/'index.html').read_text(encoding='utf-8')
        self.assertIn('techShowAllConnections',html)
        self.assertIn('connection-highlight',app)
        self.assertIn('connection-hidden',app)
        self.assertIn("$('#techViewMode')?.value==='modder'",app)
        self.assertIn('fallback-marker',app)
        self.assertIn('modder-node-id',app)


if __name__ == '__main__': unittest.main()

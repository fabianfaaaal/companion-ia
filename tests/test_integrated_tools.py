import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import io,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from companion.policy import Policy,Denied,State
from companion.agenda import AgendaStore
from companion.memory_store import MemoryStore,MemoryStoreError
from companion.media_store import MediaStore
from companion.web_search import SearchClient,SearchError,parse_results,public_url
try:
    from PIL import Image
    PIL=True
except ImportError:PIL=False


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/"memory.sqlite"
        self.policy=Policy()
        for op in ("memory.read","memory.write","agenda.read","agenda.write","media.read","media.write"):self.policy.grant(op)
    def tearDown(self):self.temp.cleanup()
    def test_off_does_not_create_database_or_files(self):
        for store,call in ((AgendaStore(self.path,self.policy),lambda s:s.list_items()),
                           (MediaStore(self.path,self.policy),lambda s:s.list_moments())):
            with self.assertRaises(Denied):call(store)
        self.assertFalse(self.path.exists())
    def test_reminder_utc_due_delivery_and_cancel(self):
        self.policy.set_memory(True);store=AgendaStore(self.path,self.policy)
        due=datetime.now(timezone.utc)+timedelta(hours=1)
        item=store.add("Prueba","General",due,"alarm",False)
        self.assertEqual(store.due(datetime.now(timezone.utc)),[])
        self.assertEqual(store.due(due+timedelta(seconds=1))[0]["id"],item["id"])
        self.assertTrue(store.mark_delivered(item["id"]))
        self.assertEqual(store.due(due+timedelta(seconds=1)),[])
        self.assertFalse(store.cancel(item["id"]))
        other=store.add("Otra","",due);self.assertTrue(store.cancel(other["id"]))
        with self.assertRaises(ValueError):store.add("Sin zona","",datetime.now())
    def test_private_blocks_agenda(self):
        self.policy.set_memory(True);self.policy.enter_private()
        with self.assertRaises(Denied):AgendaStore(self.path,self.policy).list_items()
        self.assertFalse(self.path.exists())
    @unittest.skipUnless(PIL,"Pillow not installed")
    def test_shared_archive_photo_dedup_delete_and_notes(self):
        self.policy.set_memory(True)
        image=self.root/"synthetic.png";Image.new("RGB",(60,40),"green").save(image)
        notes=MemoryStore(self.path,self.policy);notes.save_note("Nota","General")
        agenda=AgendaStore(self.path,self.policy);agenda.add("Aviso","",datetime.now(timezone.utc)+timedelta(hours=1))
        media=MediaStore(self.path,self.policy)
        first=media.import_photo(image,"Semilla","Foto de prueba")
        second=media.import_photo(image,"Semilla dos","Otro momento")
        self.assertEqual(first["asset_id"],second["asset_id"])
        self.assertEqual(len(media.search("Semilla")),2)
        original=media.asset_path(first["id"]);self.assertEqual(original.read_bytes(),image.read_bytes())
        self.assertTrue(media.delete_moment(first["id"]));self.assertTrue(original.exists())
        self.assertTrue(media.delete_moment(second["id"]));self.assertFalse(original.exists())
        self.assertEqual(media.search("Semilla"),[])
        self.assertEqual(len(notes.list_notes()),1);self.assertEqual(len(agenda.list_items()),1)
    @unittest.skipUnless(PIL,"Pillow not installed")
    def test_invalid_photo_creates_no_archive(self):
        self.policy.set_memory(True);bad=self.root/"bad.png";bad.write_text("not an image")
        with self.assertRaises(MemoryStoreError):MediaStore(self.path,self.policy).import_photo(bad,"Test","")
        self.assertFalse(self.path.exists())
    def test_unrelated_revoke_does_not_invalidate_live_request(self):
        self.policy.grant("local.request");lease=self.policy.begin("local.request")
        self.policy.revoke("unused.permission")
        self.assertTrue(self.policy.publish(lease,lambda:True))


class WebTests(unittest.TestCase):
    def test_public_urls_and_rss_metadata(self):
        for url in ("file:///secret","http://127.0.0.1","http://192.168.1.1","http://u:p@example.com","https://example.com:445"):
            self.assertFalse(public_url(url))
        raw=b"<rss><channel><item><title>Test</title><link>https://example.com</link><description>Snippet</description></item></channel></rss>"
        results=parse_results(raw,"Bing");self.assertEqual(len(results),1);self.assertEqual(results[0]["snippet"],"Snippet")
    def test_entities_and_invalid_json_rejected(self):
        with self.assertRaises(SearchError):parse_results(b'<!DOCTYPE rss [<!ENTITY a "x">]><rss/>',"Bing")
        with self.assertRaises(SearchError):parse_results(b'not json',"Wikipedia")
    def test_cancel_before_connect_and_invalid_provider(self):
        client=SearchClient();client.cancel()
        with patch("companion.web_search.http.client.HTTPSConnection") as connect:
            with self.assertRaises(SearchError):client.search("test")
            connect.assert_not_called()
        with self.assertRaises(SearchError):SearchClient().search("test","arbitrary")
    def test_redirect_not_followed(self):
        conn=MagicMock();conn.getresponse.return_value.status=302
        with patch("companion.web_search.http.client.HTTPSConnection",return_value=conn) as factory:
            with self.assertRaises(SearchError):SearchClient().search("synthetic")
        factory.assert_called_once_with("www.bing.com",timeout=3)
        self.assertEqual(conn.request.call_args.args[0],"GET")


try:
    from PySide6.QtWidgets import QApplication
    from companion.desktop import Window
    from companion.environment_panel import EnvironmentPanel
    QT=True
except ImportError:QT=False


@unittest.skipUnless(QT,"Qt not installed")
class ToolsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.w=Window(Path(self.temp.name)/"privacy.json",auto_connect=False)
    def tearDown(self):
        self.w.web_panel._workers.clear();self.w.close();self.w.deleteLater();self.app.processEvents();self.temp.cleanup()
    def test_refresh_does_not_invalidate_chat_and_tabs_survive(self):
        lease=self.w.policy.begin("local.request");self.w._refresh();self.app.processEvents()
        self.assertTrue(self.w.policy.publish(lease,lambda:True));self.assertEqual(self.w.tabs.count(),5)
        self.w.transcript.setPlainText("synthetic")
    def test_environment_no_poll_without_permission_and_private_clears(self):
        panel=EnvironmentPanel(self.w.policy,read_title=lambda:"Synthetic editor")
        panel._tick();self.assertEqual(panel.context(),[])
        self.w.policy.grant("sensor.foreground")
        panel.allow.blockSignals(True);panel.allow.setChecked(True);panel.allow.blockSignals(False)
        panel.share.setChecked(True);panel._tick()
        self.assertIn("Synthetic editor",panel.context()[0]["body"])
        panel.stamp=0;self.assertNotIn("Synthetic editor",panel.context()[0]["body"])
        panel.stop();self.assertEqual(panel.context(),[]);panel.deleteLater()
    def test_web_late_results_rejected(self):
        panel=self.w.web_panel;self.w.policy.grant("web.search");panel._lease=self.w.policy.begin("web.search")
        old=panel._job;self.w.toggle_private();panel._result(old,[{"title":"late","url":"https://example.com","snippet":"x"}])
        self.assertEqual(panel.results,[]);self.assertEqual(self.w.policy.state,State.PRIVATE)
    def test_web_stop_timeout_blocks_private(self):
        worker=MagicMock();worker.wait.return_value=False
        self.w.web_panel._workers.add(worker);self.w.toggle_private()
        self.assertEqual(self.w.policy.state,State.ERROR)
    def test_agenda_explicit_activation_and_private(self):
        self.w.memory_panel.set_enabled(True);panel=self.w.agenda_panel
        self.assertFalse(panel.timer.isActive());panel.set_active(True);self.assertTrue(panel.timer.isActive())
        self.w.toggle_private();self.assertFalse(panel.timer.isActive());self.assertEqual(panel.listing.count(),0)


    def test_due_notice_delivered_once_and_private_clears_banner(self):
        from datetime import datetime, timedelta, timezone
        self.w.memory_panel.set_enabled(True)
        panel = self.w.agenda_panel
        now = datetime.now(timezone.utc)
        item = panel.store.add("Synthetic due", "notice", now + timedelta(seconds=60))
        panel.set_active(True)
        alerts = []
        panel.alert.connect(alerts.append)
        with patch("companion.agenda_panel.datetime") as clock:
            clock.now.return_value = now + timedelta(seconds=61)
            panel._tick()
            panel._tick()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["id"], item["id"])
        self.assertEqual(panel.store.list_items()[0]["status"], "delivered")
        self.w.toggle_private()
        self.assertEqual(panel.banner.text(), "")

    def test_revoking_environment_context_cancels_derived_session(self):
        self.w.history = [{"role": "assistant", "content": "Synthetic window"}]
        panel = self.w.environment
        panel.share.setChecked(True)
        panel.share.setChecked(False)
        self.assertEqual(self.w.history, [])

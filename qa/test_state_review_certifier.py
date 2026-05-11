#!/usr/bin/env python3
"""Unit tests for review-screen certification planning."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from state_automation_profiles import next_certification_gate  # noqa: E402
from state_review_certifier import classify_review_signals, dry_run_certify, notional_filing, redact_url  # noqa: E402


class StateReviewCertifierTests(unittest.TestCase):
    def test_notional_filing_is_state_specific_and_non_sensitive(self):
        filing = notional_filing("CA")
        self.assertEqual(filing["state"], "CA")
        self.assertIn("SOSFiler CA Review Test LLC", filing["business_name"])
        self.assertNotIn("ssn", " ".join(filing.keys()).lower())

    def test_generic_reachable_page_needs_state_script(self):
        result = classify_review_signals(
            state="CA",
            body="Start a business online. Review fees and payment methods.",
            scripted=False,
            http_status=200,
            url="https://example.gov",
        )
        self.assertEqual(result["status"], "needs_state_script")
        self.assertTrue(result["signals"]["review_visible"])

    def test_challenge_requires_trusted_access(self):
        result = classify_review_signals(
            state="NY",
            body="Just a moment Cloudflare security verification",
            scripted=True,
            http_status=403,
        )
        self.assertEqual(result["status"], "trusted_access_required")
        self.assertTrue(result["signals"]["challenge"])

    def test_imperva_access_denied_requires_trusted_access(self):
        result = classify_review_signals(
            state="CA",
            body="Access denied Error 15 This request was blocked by our security service Powered by Imperva",
            scripted=True,
            http_status=200,
        )
        self.assertEqual(result["status"], "trusted_access_required")
        self.assertTrue(result["signals"]["challenge"])

    def test_turnstile_requires_trusted_access(self):
        result = classify_review_signals(
            state="OK",
            body="<input name='cf-turnstile-response' id='cf-chl-widget_response'>",
            scripted=True,
            http_status=200,
        )
        self.assertEqual(result["status"], "trusted_access_required")
        self.assertTrue(result["signals"]["challenge"])

    def test_recaptcha_requires_trusted_access(self):
        result = classify_review_signals(
            state="CT",
            body="<textarea name='g-recaptcha-response'></textarea>",
            scripted=True,
            http_status=200,
        )
        self.assertEqual(result["status"], "trusted_access_required")
        self.assertTrue(result["signals"]["challenge"])

    def test_redact_url_removes_sensitive_login_query_values(self):
        url = redact_url("https://login.ct.gov/ctidentity/login?ReqID=abc123&goto=https://service.example/path&safe=ok")
        self.assertIn("ReqID=%5BREDACTED%5D", url)
        self.assertIn("goto=%5BREDACTED%5D", url)
        self.assertIn("safe=ok", url)

    def test_scripted_review_screen_can_complete(self):
        result = classify_review_signals(
            state="TX",
            body="Review your filing summary. Fees are shown below.",
            scripted=True,
            http_status=200,
        )
        self.assertEqual(result["status"], "complete")

    def test_dry_run_marks_operator_lane_as_trusted_access_required(self):
        cert = dry_run_certify({
            "state": "AZ",
            "automation_lane": "operator_assisted_browser_provider",
            "portal_urls": ["https://ecorp.azcc.gov/"],
            "next_gate": {"code": "trusted_access_checkpoint", "label": "Trusted Access checkpoint"},
        })
        self.assertEqual(cert.status, "trusted_access_required")

    def test_dry_run_marks_california_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "CA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://bizfileonline.sos.ca.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ca_bizfile_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_colorado_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "CO",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.sos.state.co.us/pubs/business/fileAForm.html"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "co_business_filing_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_oklahoma_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "OK",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.sos.ok.gov/corp/filing.aspx"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ok_sos_wizard_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_alabama_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "AL",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.alabamainteractive.org/sos/introduction_input.action"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "al_sos_online_services_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_connecticut_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "CT",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://business.ct.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ct_business_services_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_arkansas_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "AR",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.ark.org/sos/corpfilings/index.php"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ar_sos_corpfilings_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_alaska_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "AK",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.commerce.alaska.gov/web/cbpl/Corporations/CreateFileNewEntity"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ak_cbpl_create_entity_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_arizona_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "AZ",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://ecorp.azcc.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "az_ecorp_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_delaware_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "DE",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://corp.delaware.gov/document-upload-service-information/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "de_ecorp_document_upload_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_florida_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "FL",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://efile.sunbiz.org/llc_file.html"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "fl_sunbiz_efile_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_georgia_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "GA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://ecorp.sos.ga.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ga_ecorp_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_hawaii_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "HI",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://hbe.ehawaii.gov/BizEx/home.eb"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "hi_bizex_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_iowa_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "IA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://filings.sos.iowa.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ia_fast_track_filing_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_idaho_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "ID",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://sosbiz.idaho.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "id_sosbiz_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_indiana_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "IN",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://inbiz.in.gov/BOS/Home/Index"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "in_inbiz_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_illinois_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "IL",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://apps.ilsos.gov/corporatellc/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "il_corporatellc_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_kansas_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "KS",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.sos.ks.gov/eforms/user_login.aspx?frm=BS"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ks_eforms_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_kentucky_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "KY",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://web.sos.ky.gov/fasttrack/default.aspx"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ky_fasttrack_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_louisiana_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "LA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://geauxbiz.sos.la.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "la_geauxbiz_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_massachusetts_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://corp.sec.state.ma.us/corpweb/loginsystem/ListNewFilings.aspx?FilingMethod=I"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ma_corpweb_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_maryland_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MD",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://egov.maryland.gov/businessexpress"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "md_business_express_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_maine_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "ME",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://apps1.web.maine.gov/cgi-bin/online/aro/index.pl"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "me_aro_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_michigan_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MI",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://mibusinessregistry.lara.state.mi.us"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "mi_mibusiness_registry_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_minnesota_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MN",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://mblsportal.sos.state.mn.us/Business"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "mn_mbls_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_missouri_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MO",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://bsd.sos.mo.gov/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "mo_bsd_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_mississippi_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MS",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://corp.sos.ms.gov/corp/portal/c/portal.aspx"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ms_corp_portal_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_montana_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "MT",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://biz.sosmt.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "mt_biz_sosmt_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_north_carolina_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NC",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.sosnc.gov/online_filing/filing/creation"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nc_sosnc_online_filing_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_north_dakota_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "ND",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://firststop.sos.nd.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nd_firststop_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_nebraska_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NE",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.nebraska.gov/apps-sos-edocs"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ne_corp_filing_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_new_hampshire_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NH",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://quickstart.sos.nh.gov/online/Account/LoginPage?LoginType=CreateNewBusiness"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nh_quickstart_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_new_jersey_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NJ",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.njportal.com/DOR/BusinessFormation/CompanyInformation/BusinessName"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nj_business_formation_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_new_mexico_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NM",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://enterprise.sos.nm.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nm_enterprise_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_nevada_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NV",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.nvsilverflume.gov/home"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "nv_silverflume_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_new_york_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "NY",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://businessexpress.ny.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ny_business_express_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_ohio_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "OH",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://www.ohiobusinesscentral.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "oh_obc_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_oregon_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "OR",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://secure.sos.state.or.us/cbrmanager/index.action"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "or_cbr_manager_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_pennsylvania_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "PA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://file.dos.pa.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "pa_file_dos_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_rhode_island_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "RI",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://business.sos.ri.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ri_business_sos_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_south_carolina_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "SC",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://businessfilings.sc.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "sc_business_filings_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_south_dakota_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "SD",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://sosenterprise.sd.gov/BusinessServices"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "sd_sosenterprise_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_tennessee_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "TN",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://tnbear.tn.gov/Ecommerce/FilingSearch.aspx"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "tn_tnbear_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_utah_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "UT",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://businessregistration.utah.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "ut_business_registration_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_virginia_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "VA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://cis.scc.virginia.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "va_cis_scc_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_vermont_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "VT",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://bizfilings.vermont.gov/online"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "vt_bizfilings_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_washington_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "WA",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://ccfs.sos.wa.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "wa_ccfs_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_west_virginia_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "WV",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://onestop.wv.gov"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "wv_onestop_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_wisconsin_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "WI",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://apps.dfi.wi.gov/apps/CorpFormation/"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "wi_wdfi_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_wyoming_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "WY",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://wyobiz.wyo.gov/Business/FilingSearch.aspx"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "wy_wyobiz_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_dry_run_marks_dc_as_scripted_browser_certification(self):
        cert = dry_run_certify({
            "state": "DC",
            "automation_lane": "browser_profile_automation",
            "portal_urls": ["https://corponline.dlcp.dc.gov/Home.aspx/Landing"],
            "next_gate": {"code": "review_screen", "label": "Review screen certification"},
        })
        self.assertEqual(cert.script_id, "dc_corponline_v1")
        self.assertEqual(cert.status, "pending_browser_certification")

    def test_applicant_data_required_remains_in_worklist(self):
        gate = next_certification_gate({
            "certification_gates": [
                {"code": "portal_entry", "status": "complete"},
                {"code": "review_screen", "status": "applicant_data_required"},
            ],
        })
        self.assertEqual(gate["code"], "review_screen")


if __name__ == "__main__":
    unittest.main()

// Youcef ADDA | GEODECI
// FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
// EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
// AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
// Site / Website / الموقع : https://geodeci.xyz/
// Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
// Détails / Details / التفاصيل : SIGNATURE.md
// ============================================================================
/**
 * ST-AIR Usage Diag - Google Apps Script
 * 
 * Web App qui recoit les diagnostics d'execution du plugin ST-AIR
 * et structure les donnees dans une feuille dediee avec mise en forme.
 */

const SPREADSHEET_ID = "1dja4SPMZIow94EulnaGvaeZWusIqwnr4VCHrNrycpts";
const SHEET_NAME = "ST-AIR";
const DASHBOARD_NAME = "Dashboard Global";
const DASHBOARD_ENTREPRISES = "Dashboard Entreprises";
const DASHBOARD_DERNIER_RUN = "Dernier Run";

/**
 * Handler POST : recoit le JSON du plugin et ecrit une ligne dans la feuille.
 */
const EXCLUDED_USERS = ["yadda"];

function isExcludedUser_(data) {
  if (!data) return false;
  var candidates = [data.windows_user, data.userprofile, data.email, data.windows_email_auto];
  for (var i = 0; i < candidates.length; i++) {
    var val = String(candidates[i] || "").trim().toLowerCase();
    if (!val) continue;
    var shortName = val.split("\\").pop().split("/").pop().split("@")[0].trim();
    if (EXCLUDED_USERS.indexOf(shortName) >= 0) return true;
  }
  return false;
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    if (isExcludedUser_(data)) {
      return jsonResponse({status: "ok", ignored: true});
    }
    var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    
    // Cree ou recupere la feuille diagnostic
    var sheet = getOrCreateDiagSheet(ss);
    
    // Ecrit la ligne de donnees
    var row = buildRow(data);
    sheet.appendRow(row);
    var lastRow = sheet.getLastRow();
    if (lastRow > 2) {
      sheet.getRange(2, 1, lastRow - 1, row.length).sort([
        { column: 1, ascending: false }
      ]);
    }
    
    // Met a jour les dashboards
    updateDashboardGlobal(ss);
    updateDashboardEntreprises(ss);
    updateDashboardDernierRun(ss);
    
    return jsonResponse({status: "ok", row: sheet.getLastRow()});
    
  } catch (err) {
    return jsonResponse({status: "error", message: err.toString()}, 500);
  }
}

/**
 * Handler GET : health check simple.
 */
function doGet(e) {
  return ContentService.createTextOutput("ST-AIR Diagnostic endpoint actif.")
    .setMimeType(ContentService.MimeType.TEXT);
}

/**
 * Cree ou retourne la feuille diagnostic avec mise en forme initiale.
 */
function getOrCreateDiagSheet(ss) {
  var sheet = ss.getSheetByName(SHEET_NAME);
  
  if (sheet) {
    return sheet;
  }
  
  // Cree la feuille
  sheet = ss.insertSheet(SHEET_NAME);
  
  // --- En-tetes ---
  var headers = [
    "Date/Heure", "Hostname", "Adresse MAC", "IP", "Utilisateur Windows",
    "Domaine", "DNS Domain", "Logon Server", "Userprofile",
    "OS", "Arch", "Python", "Entreprise", "Email", "Email Windows (auto)",
    "Version Plugin", "Version QGIS", "SRO", "Modules executes", "Couches QGIS",
    "Total modules", "OK", "Erreurs", "Duree totale (s)",
    "Resume modules", "Erreurs log (JSON)", "Annule", "Erreur rapport"
  ];
  
  var headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setValues([headers]);
  
  // Style en-tete
  headerRange.setFontWeight("bold")
    .setBackground("#4285f4")
    .setFontColor("#ffffff")
    .setVerticalAlignment("middle");
  
  // Gele la premiere ligne
  sheet.setFrozenRows(1);
  
  // Largeurs de colonnes
  sheet.setColumnWidth(1, 180);   // timestamp
  sheet.setColumnWidth(2, 140);   // hostname
  sheet.setColumnWidth(3, 130);   // mac
  sheet.setColumnWidth(4, 120);   // ip
  sheet.setColumnWidth(5, 140);   // windows user
  sheet.setColumnWidth(6, 100);   // domain
  sheet.setColumnWidth(7, 120);   // dns domain
  sheet.setColumnWidth(8, 120);   // logon server
  sheet.setColumnWidth(9, 200);   // userprofile
  sheet.setColumnWidth(10, 120);  // os
  sheet.setColumnWidth(11, 80);   // arch
  sheet.setColumnWidth(12, 80);   // python
  sheet.setColumnWidth(13, 180);  // company
  sheet.setColumnWidth(14, 200);  // email
  sheet.setColumnWidth(15, 200);  // email windows auto
  sheet.setColumnWidth(16, 100);  // plugin version
  sheet.setColumnWidth(17, 100);  // qgis version
  sheet.setColumnWidth(18, 150);  // sro
  sheet.setColumnWidth(19, 200);  // modules
  sheet.setColumnWidth(20, 200);  // layers
  sheet.setColumnWidth(21, 80);   // total
  sheet.setColumnWidth(22, 50);   // ok
  sheet.setColumnWidth(23, 60);   // errors
  sheet.setColumnWidth(24, 100);  // duration
  sheet.setColumnWidth(25, 400);  // resume modules
  sheet.setColumnWidth(26, 300);  // errors JSON
  sheet.setColumnWidth(27, 60);   // cancelled
  sheet.setColumnWidth(28, 200);  // report error
  
  // Regles de mise en forme conditionnelle
  applyConditionalFormatting(sheet, headers.length);
  
  // Validation pour la colonne "Annule" (OUI/NON)
  var cancelRange = sheet.getRange(2, 27, 1000, 1);
  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["OUI", "NON"], true)
    .setAllowInvalid(false)
    .build();
  cancelRange.setDataValidation(rule);
  
  return sheet;
}

/**
 * Construit un tableau de valeurs a partir du JSON recu.
 */
function buildRow(data) {
  var global = data.global || {};
  
  return [
    data.timestamp || "",
    data.hostname || "",
    data.mac_address || "",
    data.ip_address || "",
    data.windows_user || "",
    data.domain || "",
    data.dns_domain || "",
    data.logon_server || "",
    data.userprofile || "",
    data.os || "",
    data.arch || "",
    data.python_version || "",
    data.company || "",
    data.email || "",
    data.windows_email_auto || "",
    data.plugin_version || "",
    data.qgis_version || "",
    data.sro || "",
    Array.isArray(data.modules) ? data.modules.join(", ") : "",
    data.layers || "",
    global.modules_total || 0,
    global.modules_ok || 0,
    global.modules_error || 0,
    global.duration_total_sec || 0,
    data.module_summary || "",
    JSON.stringify(data.error_tail || []),
    data.cancelled ? "OUI" : "NON",
    data.report_error || ""
  ];
}

/**
 * Applique les regles de mise en forme conditionnelle.
 */
function applyConditionalFormatting(sheet, numCols) {
  var lastRow = Math.max(sheet.getMaxRows(), 100);
  
  // Colonne Erreurs (W) -> rouge si > 0
  var errRange = sheet.getRange(2, 23, lastRow, 1);
  var errRule = SpreadsheetApp.newConditionalFormatRule()
    .whenNumberGreaterThan(0)
    .setBackground("#f4cccc")
    .setFontColor("#cc0000")
    .setRanges([errRange])
    .build();
  
  // Colonne OK (V) -> vert si = Total
  var okRange = sheet.getRange(2, 22, lastRow, 1);
  var okRule = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied("=$V2=$U2")
    .setBackground("#d9ead3")
    .setFontColor("#38761d")
    .setRanges([okRange])
    .build();
  
  // Colonne Annule (AA) -> orange si OUI
  var cancelRange = sheet.getRange(2, 27, lastRow, 1);
  var cancelRule = SpreadsheetApp.newConditionalFormatRule()
    .whenTextEqualTo("OUI")
    .setBackground("#fce5cd")
    .setFontColor("#b45f06")
    .setRanges([cancelRange])
    .build();
  
  // Colonne Erreur rapport (AB) -> rouge si non vide
  var repErrRange = sheet.getRange(2, 28, lastRow, 1);
  var repErrRule = SpreadsheetApp.newConditionalFormatRule()
    .whenCellNotEmpty()
    .setBackground("#f4cccc")
    .setFontColor("#cc0000")
    .setRanges([repErrRange])
    .build();
  
  var rules = [
    errRule,
    okRule,
    cancelRule,
    repErrRule
  ];
  
  sheet.setConditionalFormatRules(rules);
}

/**
 * Cree ou met a jour le dashboard de synthese.
 */
// =============================================================================
// DASHBOARD GLOBAL
// =============================================================================

function updateDashboardGlobal(ss) {
  var dataSheet = ss.getSheetByName(SHEET_NAME);
  if (!dataSheet) return;
  
  var dash = ss.getSheetByName(DASHBOARD_NAME);
  if (!dash) {
    dash = ss.insertSheet(DASHBOARD_NAME);
    setupDashboardGlobal(dash);
  }
  
  var lastRow = dataSheet.getLastRow();
  if (lastRow < 2) return;
  
  dash.getRange("B2").setValue(lastRow - 1);
  dash.getRange("B3").setFormula('=COUNTIF(\'' + SHEET_NAME + '\'!W2:W' + lastRow + ',">0")');
  dash.getRange("B4").setFormula('=COUNTIF(\'' + SHEET_NAME + '\'!AA2:AA' + lastRow + ',"OUI")');
  dash.getRange("B5").setFormula('=COUNTUNIQUE(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ')');
  dash.getRange("B6").setFormula('=COUNTUNIQUE(\'' + SHEET_NAME + '\'!R2:R' + lastRow + ')');
  dash.getRange("B7").setFormula('=AVERAGE(\'' + SHEET_NAME + '\'!X2:X' + lastRow + ')');
  dash.getRange("B8").setFormula('=MAX(\'' + SHEET_NAME + '\'!A2:A' + lastRow + ')');
  dash.getRange("B9").setFormula('=SUM(\'' + SHEET_NAME + '\'!V2:V' + lastRow + ')');
  dash.getRange("B10").setFormula('=SUM(\'' + SHEET_NAME + '\'!W2:W' + lastRow + ')');
  dash.getRange("B11").setFormula('=COUNTA(\'' + SHEET_NAME + '\'!E2:E' + lastRow + ')');
  dash.getRange("B12").setFormula('=COUNTUNIQUE(\'' + SHEET_NAME + '\'!E2:E' + lastRow + ')');
}

function setupDashboardGlobal(dash) {
  dash.setColumnWidth(1, 240);
  dash.setColumnWidth(2, 140);
  
  var labels = [
    ["METRIQUE", "VALEUR"],
    ["Executions totales", 0],
    ["Executions avec erreurs", 0],
    ["Executions annulees", 0],
    ["Entreprises distinctes", 0],
    ["SRO distincts", 0],
    ["Duree moyenne (s)", 0],
    ["Derniere execution", ""],
    ["Total modules OK (cumul)", 0],
    ["Total modules KO (cumul)", 0],
    ["Utilisateurs Windows", 0],
    ["Utilisateurs uniques", 0]
  ];
  
  var range = dash.getRange(1, 1, labels.length, 2);
  range.setValues(labels);
  
  dash.getRange(1, 1, 1, 2).setFontWeight("bold")
    .setBackground("#4285f4")
    .setFontColor("#ffffff");
  dash.getRange(2, 1, labels.length - 1, 1).setFontWeight("bold");
  dash.getRange(2, 2, labels.length - 1, 1).setHorizontalAlignment("center");
  dash.setFrozenRows(1);
}


// =============================================================================
// DASHBOARD ENTREPRISES
// =============================================================================

function updateDashboardEntreprises(ss) {
  var dataSheet = ss.getSheetByName(SHEET_NAME);
  if (!dataSheet) return;
  
  var dash = ss.getSheetByName(DASHBOARD_ENTREPRISES);
  if (!dash) {
    dash = ss.insertSheet(DASHBOARD_ENTREPRISES);
    setupDashboardEntreprises(dash);
  }
  
  var lastRow = dataSheet.getLastRow();
  if (lastRow < 2) return;
  
  // Recupere les entreprises uniques
  var companies = dataSheet.getRange(2, 13, lastRow - 1, 1).getValues();
  var uniqueCompanies = [];
  var seen = {};
  for (var i = 0; i < companies.length; i++) {
    var c = companies[i][0];
    if (c && c.trim() && !seen[c]) {
      seen[c] = true;
      uniqueCompanies.push(c);
    }
  }
  uniqueCompanies.sort();
  
  // Efface les anciennes donnees (sauf en-tete)
  var oldRows = dash.getLastRow();
  if (oldRows > 1) {
    dash.getRange(2, 1, oldRows - 1, 10).clearContent();
  }
  
  // Remplit les lignes
  for (var i = 0; i < uniqueCompanies.length; i++) {
    var row = i + 2;
    var c = uniqueCompanies[i].replace(/"/g, '\\"');
    dash.getRange(row, 1).setValue(uniqueCompanies[i]);
    dash.getRange(row, 2).setFormula('=COUNTIF(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '")');
    dash.getRange(row, 3).setFormula('=SUMIF(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '",\'' + SHEET_NAME + '\'!V2:V' + lastRow + ')');
    dash.getRange(row, 4).setFormula('=SUMIF(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '",\'' + SHEET_NAME + '\'!W2:W' + lastRow + ')');
    dash.getRange(row, 5).setFormula('=COUNTIFS(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '",\'' + SHEET_NAME + '\'!AA2:AA' + lastRow + ',"OUI")');
    dash.getRange(row, 6).setFormula('=AVERAGEIF(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '",\'' + SHEET_NAME + '\'!X2:X' + lastRow + ')');
    dash.getRange(row, 7).setFormula('=MAXIFS(\'' + SHEET_NAME + '\'!A2:A' + lastRow + ',\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '")');
    dash.getRange(row, 8).setFormula('=COUNTUNIQUEIFS(\'' + SHEET_NAME + '\'!R2:R' + lastRow + ',\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '")');
    dash.getRange(row, 9).setFormula('=SUMPRODUCT((\'' + SHEET_NAME + '\'!M2:M' + lastRow + '="' + c + '")*(\'' + SHEET_NAME + '\'!W2:W' + lastRow + '>0))');
    dash.getRange(row, 10).setFormula('=COUNTIFS(\'' + SHEET_NAME + '\'!M2:M' + lastRow + ',"' + c + '",\'' + SHEET_NAME + '\'!W2:W' + lastRow + ',0,\'' + SHEET_NAME + '\'!AA2:AA' + lastRow + ',"NON")');
  }
  
  // Barre de progression visuelle (taux de succes)
  if (uniqueCompanies.length > 0) {
    for (var i = 0; i < uniqueCompanies.length; i++) {
      var row = i + 2;
      dash.getRange(row, 3).setNumberFormat("0");
      dash.getRange(row, 4).setNumberFormat("0");
      dash.getRange(row, 6).setNumberFormat("0.0");
    }
  }
}

function setupDashboardEntreprises(dash) {
  dash.setColumnWidth(1, 200);
  dash.setColumnWidth(2, 110);
  dash.setColumnWidth(3, 80);
  dash.setColumnWidth(4, 80);
  dash.setColumnWidth(5, 80);
  dash.setColumnWidth(6, 130);
  dash.setColumnWidth(7, 180);
  dash.setColumnWidth(8, 100);
  dash.setColumnWidth(9, 120);
  dash.setColumnWidth(10, 120);
  
  var headers = [
    "Entreprise", "Executions", "Modules OK", "Modules KO", "Annules",
    "Duree moyenne (s)", "Derniere execution", "SRO uniques",
    "Runs avec erreur", "Runs sans erreur"
  ];
  dash.getRange(1, 1, 1, headers.length).setValues([headers]);
  dash.getRange(1, 1, 1, headers.length).setFontWeight("bold")
    .setBackground("#4285f4")
    .setFontColor("#ffffff");
  dash.setFrozenRows(1);
}


// =============================================================================
// DASHBOARD DERNIER RUN
// =============================================================================

function updateDashboardDernierRun(ss) {
  var dataSheet = ss.getSheetByName(SHEET_NAME);
  if (!dataSheet) return;
  
  var dash = ss.getSheetByName(DASHBOARD_DERNIER_RUN);
  if (!dash) {
    dash = ss.insertSheet(DASHBOARD_DERNIER_RUN);
    setupDashboardDernierRun(dash);
  }
  
  var lastRow = dataSheet.getLastRow();
  if (lastRow < 2) return;
  
  // Ligne 2 = execution la plus recente (feuille triee par date decroissante)
  var ref = "2";
  
  // Maj des formules INDEX
  dash.getRange("B2").setFormula('=INDEX(\'' + SHEET_NAME + '\'!A:A,' + ref + ')');
  dash.getRange("B3").setFormula('=INDEX(\'' + SHEET_NAME + '\'!B:B,' + ref + ')');
  dash.getRange("B4").setFormula('=INDEX(\'' + SHEET_NAME + '\'!C:C,' + ref + ')');
  dash.getRange("B5").setFormula('=INDEX(\'' + SHEET_NAME + '\'!D:D,' + ref + ')');
  dash.getRange("B6").setFormula('=INDEX(\'' + SHEET_NAME + '\'!E:E,' + ref + ')');
  dash.getRange("B7").setFormula('=INDEX(\'' + SHEET_NAME + '\'!F:F,' + ref + ')');
  dash.getRange("B8").setFormula('=INDEX(\'' + SHEET_NAME + '\'!M:M,' + ref + ')');
  dash.getRange("B9").setFormula('=INDEX(\'' + SHEET_NAME + '\'!N:N,' + ref + ')');
  dash.getRange("B10").setFormula('=INDEX(\'' + SHEET_NAME + '\'!O:O,' + ref + ')');
  dash.getRange("B11").setFormula('=INDEX(\'' + SHEET_NAME + '\'!R:R,' + ref + ')');
  dash.getRange("B12").setFormula('=INDEX(\'' + SHEET_NAME + '\'!S:S,' + ref + ')');
  dash.getRange("B13").setFormula('=INDEX(\'' + SHEET_NAME + '\'!T:T,' + ref + ')');
  dash.getRange("B14").setFormula('=INDEX(\'' + SHEET_NAME + '\'!U:U,' + ref + ')');
  dash.getRange("B15").setFormula('=INDEX(\'' + SHEET_NAME + '\'!V:V,' + ref + ')');
  dash.getRange("B16").setFormula('=INDEX(\'' + SHEET_NAME + '\'!W:W,' + ref + ')');
  dash.getRange("B17").setFormula('=INDEX(\'' + SHEET_NAME + '\'!X:X,' + ref + ')');
  dash.getRange("B18").setFormula('=INDEX(\'' + SHEET_NAME + '\'!Y:Y,' + ref + ')');
  dash.getRange("B19").setFormula('=INDEX(\'' + SHEET_NAME + '\'!AA:AA,' + ref + ')');
}

function setupDashboardDernierRun(dash) {
  dash.setColumnWidth(1, 220);
  dash.setColumnWidth(2, 300);
  
  var labels = [
    ["CHAMP", "VALEUR"],
    ["Date/Heure", ""],
    ["Hostname", ""],
    ["Adresse MAC", ""],
    ["IP", ""],
    ["Utilisateur Windows", ""],
    ["Domaine", ""],
    ["Entreprise", ""],
    ["Email", ""],
    ["Email Windows (auto)", ""],
    ["SRO", ""],
    ["Modules executes", ""],
    ["Couches QGIS", ""],
    ["Total modules", ""],
    ["Modules OK", ""],
    ["Modules en erreur", ""],
    ["Duree totale (s)", ""],
    ["Resume modules", ""],
    ["Annule", ""]
  ];
  
  var range = dash.getRange(1, 1, labels.length, 2);
  range.setValues(labels);
  
  dash.getRange(1, 1, 1, 2).setFontWeight("bold")
    .setBackground("#4285f4")
    .setFontColor("#ffffff");
  dash.getRange(2, 1, labels.length - 1, 1).setFontWeight("bold");
  dash.getRange(18, 2).setWrap(true);  // Resume modules
  dash.setFrozenRows(1);
  
  // Colore les champs d'etat
  dash.getRange(15, 1, 1, 1).setBackground("#d9ead3");  // OK vert
  dash.getRange(16, 1, 1, 1).setBackground("#f4cccc");  // Erreurs rouge
}

/**
 * Helper : reponse JSON.
 */
function jsonResponse(obj, statusCode) {
  var output = JSON.stringify(obj);
  return ContentService.createTextOutput(output)
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Fonction de test - executer manuellement depuis l'editeur Apps Script.
 * Permet de verifier que la feuille et la mise en forme fonctionnent
 * sans lancer QGIS.
 */
function testDoPost() {
  var fake = {
    postData: {
      contents: JSON.stringify({
        timestamp: "2026-06-02T20:00:00",
        hostname: "PC-TEST",
        mac_address: "aa:bb:cc:dd:ee:ff",
        ip_address: "192.168.1.100",
        windows_user: "testuser",
        domain: "NGE",
        dns_domain: "nge.fr",
        logon_server: "\\\\DC01",
        userprofile: "C:\\Users\\testuser",
        os: "Windows 10",
        arch: "AMD64",
        python_version: "3.12.0",
        company: "TEST SA",
        email: "test@test.com",
        windows_email_auto: "test@nge.fr",
        plugin_version: "4.0.1",
        qgis_version: "3.44.0",
        sro: "63041-B1I-PMZ-00001",
        modules: ["capft", "comac"],
        layers: "cables,bpe,attaches,infra_pt_pot",
        global: {
          modules_total: 2,
          modules_ok: 2,
          modules_error: 0,
          duration_total_sec: 12.5
        },
        module_summary: "capft:14ét/991cor | comac:22ét/175OK/27éc",
        error_tail: [],
        cancelled: false,
        report_error: ""
      })
    }
  };
  var result = doPost(fake);
  Logger.log("Test result: " + result.getContent());
}

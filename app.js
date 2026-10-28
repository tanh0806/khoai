const API="http://127.0.0.1:5000/api";
let me=null;
let products=[],categories=[],warehouses=[],suppliers=[],inventory=[],transactions=[],transfers=[],lots=[],alerts=[];
let chatHistory=[];

const permissions={
  Admin:["overview","products","categories","warehouses","suppliers","transactions","transfers","lots","inventory","scanner","alerts","reports","ai","accounts","audit","profile"],
  "Thủ kho":["overview","products","categories","warehouses","transactions","transfers","lots","inventory","scanner","alerts","ai","profile"],
  "Kế toán":["overview","products","categories","warehouses","suppliers","transactions","reports","ai","profile"]
};
const roleText={
  Admin:"Toàn quyền hệ thống: dữ liệu kho, tài khoản, báo cáo, nhật ký và cấu hình nghiệp vụ.",
  "Thủ kho":"Quản lý hàng hóa, nhập/xuất, chuyển kho, lô, tồn kho, quét mã và cảnh báo trong kho được phân công.",
  "Kế toán":"Theo dõi hàng hóa, danh mục, kho, nhà cung cấp, giao dịch và báo cáo tài chính; không trực tiếp thay đổi tồn."
};
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money=n=>new Intl.NumberFormat("vi-VN").format(Number(n||0))+" ₫";
function toast(t){const e=$("#toast");e.textContent=t;e.style.display="block";setTimeout(()=>e.style.display="none",2800)}
async function req(path,opt={}){
  try{
    const r=await fetch(API+path,{headers:{"Content-Type":"application/json"},...opt});
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||"Lỗi máy chủ");
    return j;
  }catch(e){
    if(e instanceof TypeError) throw new Error("Không kết nối được backend tại 127.0.0.1:5000. Hãy chạy backend/app.py.");
    throw e;
  }
}
function can(page){return !!me&&permissions[me.role]?.includes(page)}
function showPage(p){
  if(!can(p)){toast("Vai trò hiện tại không có quyền vào chức năng này.");return}
  $$(".page").forEach(x=>x.classList.remove("active"));
  $("#page-"+p)?.classList.add("active");
  $$(".nav").forEach(x=>x.classList.toggle("active",x.dataset.page===p));
  if(p==="accounts")loadAccounts();
  if(p==="audit")loadAudit();
  if(p==="alerts")loadAlerts();
  if(p==="reports")loadReports();
  if(p==="profile")renderProfile();
}
$$(".nav").forEach(b=>b.onclick=()=>showPage(b.dataset.page));

function authView(name){["loginView","forgotView"].forEach(id=>$("#"+id).classList.toggle("hidden",id!==name))}
$("#forgotLink").onclick=()=>authView("forgotView");
$("#backLogin").onclick=()=>authView("loginView");
$("#role").onchange=()=>{const m={Admin:"admin@khoai.vn","Thủ kho":"thukho@khoai.vn","Kế toán":"ketoan@khoai.vn"};$("#email").value=m[$("#role").value]};

$("#loginForm").onsubmit=async e=>{
  e.preventDefault();$("#loginError").textContent="";
  try{
    const j=await req("/login",{method:"POST",body:JSON.stringify({email:$("#email").value,password:$("#password").value,role:$("#role").value})});
    me=j.user;sessionStorage.setItem("khoai_user",JSON.stringify(me));start();
  }catch(x){$("#loginError").textContent=x.message}
};
$("#forgotForm").onsubmit=async e=>{
  e.preventDefault();$("#forgotError").textContent="";
  if($("#newPassword").value!==$("#newPassword2").value){$("#forgotError").textContent="Mật khẩu nhập lại không khớp.";return}
  try{
    await req("/forgot-password",{method:"POST",body:JSON.stringify({email:$("#forgotEmail").value,password:$("#newPassword").value})});
    toast("Đã đổi mật khẩu.");$("#email").value=$("#forgotEmail").value;authView("loginView");
  }catch(x){$("#forgotError").textContent=x.message}
};
$("#logout").onclick=()=>{sessionStorage.removeItem("khoai_user");location.reload()};
$("#refresh").onclick=loadAll;
$("#reloadAlerts").onclick=loadAlerts;
$("#reloadReports").onclick=loadReports;

function applyPermissions(){
  $$(".nav").forEach(x=>x.classList.toggle("hidden",!can(x.dataset.page)));
  $$(".admin-op").forEach(x=>x.classList.toggle("hidden",me.role!=="Admin"));
  $$(".warehouse-op").forEach(x=>x.classList.toggle("hidden",me.role==="Kế toán"));
  if($("#roleSummary")) $("#roleSummary").textContent=roleText[me.role]||"";
}
function start(){
  $("#auth").classList.add("hidden");$("#app").classList.remove("hidden");
  const avatarChar=((me.username||me.name||"A").trim()[0]||"A").toUpperCase();
  const av=$("#userAvatar");
  if(av){
    av.textContent=avatarChar;
    av.title=`${me.name||me.username} (${me.role})`;
    av.onclick=()=>showPage("profile");
  }
  if($("#userName")) $("#userName").textContent=me.name;
  if($("#userRole")) $("#userRole").textContent=me.role;
  applyPermissions();setupSuggestions();loadAll();
}
async function loadAll(){
  try{
    const id=me.id;
    const common=await Promise.all([
      req(`/dashboard?user_id=${id}`),
      req(`/products?user_id=${id}`),
      req(`/categories?user_id=${id}`),
      req(`/warehouses?user_id=${id}`),
      req(`/transactions?user_id=${id}`)
    ]);
    const [dash,p,c,w,t]=common;
    products=p.data||[];categories=c.data||[];warehouses=w.data||[];transactions=t.data||[];
    suppliers=[];inventory=[];transfers=[];lots=[];alerts=[];
    if(can("suppliers")) suppliers=(await req(`/suppliers?user_id=${id}`)).data||[];
    if(can("inventory")) inventory=(await req(`/inventory?user_id=${id}`)).data||[];
    if(can("transfers")) transfers=(await req(`/transfers?user_id=${id}`)).data||[];
    if(can("lots")) lots=(await req(`/lots?user_id=${id}`)).data||[];
    if(can("alerts")) alerts=(await req(`/alerts?user_id=${id}`)).data||[];
    renderDashboard(dash.data||{});renderProducts();renderCategories();renderWarehouses();renderSuppliers();renderTransactions();renderTransfers();renderLots();renderInventory();renderAlerts();renderSelects();renderProfile();
    if(can("reports")) await loadReports();
  }catch(e){toast(e.message)}
}
function renderDashboard(d){
  $("#sProducts").textContent=d.product_count||0;$("#sStock").textContent=d.total_stock||0;$("#sLow").textContent=d.low_count||0;$("#sValue").textContent=money(d.inventory_value||0);
  const days=d.days||[],mx=Math.max(1,...days.flatMap(x=>[Number(x.imp||0),Number(x.exp||0)]));
  $("#chart").innerHTML=days.map(x=>`<div class="bar-day"><i class="bar imp" title="Nhập ${x.imp}" style="height:${Math.max(3,Number(x.imp||0)/mx*170)}px"></i><i class="bar exp" title="Xuất ${x.exp}" style="height:${Math.max(3,Number(x.exp||0)/mx*170)}px"></i><label>${esc(x.label)}</label></div>`).join("");
}
function renderProducts(){
  $("#productRows").innerHTML=products.map(p=>`<tr><td><b>${esc(p.code)}</b></td><td>${esc(p.name)}</td><td>${esc(p.category||"—")}</td><td>${esc(p.unit)}</td><td>${money(p.price)}</td><td>${money(p.sale_price)}</td><td>${p.min_stock}</td><td>${esc(p.barcode||"—")}</td><td><span class="badge ok">${esc(p.status)}</span></td></tr>`).join("")||`<tr><td colspan="9">Chưa có dữ liệu.</td></tr>`;
}
function renderCategories(){
  $("#categoryRows").innerHTML=categories.map(x=>`<tr><td><b>${esc(x.code)}</b></td><td>${esc(x.name)}</td><td>${esc(x.description||"")}</td></tr>`).join("")||`<tr><td colspan="3">Chưa có danh mục.</td></tr>`;
}
function renderWarehouses(){
  $("#warehouseRows").innerHTML=warehouses.map(x=>`<tr><td><b>${esc(x.code)}</b></td><td>${esc(x.name)}</td><td>${esc(x.address||"")}</td><td><span class="badge ok">${esc(x.status||"Hoạt động")}</span></td></tr>`).join("")||`<tr><td colspan="4">Chưa có kho.</td></tr>`;
}
function renderSuppliers(){
  $("#supplierRows").innerHTML=suppliers.map(x=>`<tr><td><b>${esc(x.code)}</b></td><td>${esc(x.name)}</td><td>${esc(x.phone||"")}</td><td>${esc(x.email||"")}</td><td>${esc(x.address||"")}</td></tr>`).join("")||`<tr><td colspan="5">Chưa có nhà cung cấp.</td></tr>`;
}
function renderInventory(){
  if(!can("inventory")) return;
  $("#inventoryRows").innerHTML=inventory.map(x=>`<tr><td>${esc(x.warehouse_code)}</td><td><b>${esc(x.product_code)}</b></td><td>${esc(x.product_name)}</td><td>${x.quantity}</td><td>${x.min_stock}</td><td>${money(Number(x.quantity)*Number(x.price))}</td></tr>`).join("")||`<tr><td colspan="6">Chưa có tồn kho.</td></tr>`;
}
function renderTransactions(){
  $("#txRows").innerHTML=transactions.map(t=>{
    const canAct=(me.role==="Admin"||(me.role==="Thủ kho"&&Number(me.warehouse_id)===Number(t.warehouse_id)))&&t.status==="Chờ xác nhận";
    return `<tr><td>${t.id}</td><td>${esc(t.type)}</td><td>${esc(t.warehouse_code)}</td><td>${esc(t.product_code)} - ${esc(t.product_name)}</td><td>${t.quantity}</td><td>${money(t.price)}</td><td><span class="badge ${t.status==="Đã xác nhận"?"ok":t.status==="Đã hủy"?"bad":"wait"}">${esc(t.status)}</span></td><td>${esc(t.confirmed_by_name||"—")}</td><td class="actions">${canAct?`<button class="secondary" onclick="confirmTx(${t.id})">✓ Xác nhận</button><button class="secondary danger" onclick="cancelTx(${t.id})">Hủy</button>`:"—"}</td></tr>`;
  }).join("")||`<tr><td colspan="9">Chưa có phiếu.</td></tr>`;
}
function renderTransfers(){
  if(!can("transfers")) return;
  $("#transferRows").innerHTML=transfers.map(t=>{
    const canAct=(me.role==="Admin"||(me.role==="Thủ kho"&&Number(me.warehouse_id)===Number(t.from_warehouse_id)))&&t.status==="Chờ xác nhận";
    return `<tr><td>${t.id}</td><td>${esc(t.from_code)}</td><td>${esc(t.to_code)}</td><td>${esc(t.product_code)} - ${esc(t.product_name)}</td><td>${t.quantity}</td><td><span class="badge ${t.status==="Đã xác nhận"?"ok":t.status==="Đã hủy"?"bad":"wait"}">${esc(t.status)}</span></td><td>${canAct?`<button class="secondary" onclick="confirmTransfer(${t.id})">✓ Xác nhận</button> <button class="secondary danger" onclick="cancelTransfer(${t.id})">Hủy</button>`:"—"}</td></tr>`;
  }).join("")||`<tr><td colspan="7">Chưa có phiếu chuyển kho.</td></tr>`;
}
function renderLots(){
  if(!can("lots")) return;
  $("#lotRows").innerHTML=lots.map(x=>`<tr><td><b>${esc(x.code)}</b></td><td>${esc(x.warehouse_code)}</td><td>${esc(x.product_code)} - ${esc(x.product_name)}</td><td>${x.quantity}</td><td>${esc(x.received_date||"—")}</td><td>${esc(x.expiry_date||"—")}</td><td><span class="badge ${x.expiry_state==="Đã hết hạn"?"bad":x.expiry_state==="Sắp hết hạn"?"warn":"ok"}">${esc(x.expiry_state)}</span></td></tr>`).join("")||`<tr><td colspan="7">Chưa có lô hàng.</td></tr>`;
}
function renderAlerts(){
  if(!can("alerts")) return;
  $("#alertRows").innerHTML=alerts.map(x=>`<tr><td><span class="badge ${x.level==="Nguy hiểm"?"bad":"warn"}">${esc(x.level)}</span></td><td>${esc(x.type)}</td><td><b>${esc(x.subject)}</b></td><td>${esc(x.detail)}</td></tr>`).join("")||`<tr><td colspan="4">Không có cảnh báo.</td></tr>`;
}
function renderSelects(){
  const whAllowed=warehouses.filter(w=>me.role!=="Thủ kho"||Number(w.id)===Number(me.warehouse_id));
  $("#txWarehouse").innerHTML=whAllowed.map(w=>`<option value="${w.id}">${esc(w.code)} - ${esc(w.name)}</option>`).join("");
  $("#txProduct").innerHTML=products.map(p=>`<option value="${p.id}">${esc(p.code)} - ${esc(p.name)}</option>`).join("");
  $("#txSupplier").innerHTML=`<option value="">-- Không chọn NCC --</option>`+suppliers.map(s=>`<option value="${s.id}">${esc(s.code)} - ${esc(s.name)}</option>`).join("");
  $("#pCategory").innerHTML=`<option value="">-- Danh mục --</option>`+categories.map(c=>`<option value="${c.id}">${esc(c.code)} - ${esc(c.name)}</option>`).join("");
  if(can("transfers")){
    $("#trFrom").innerHTML=whAllowed.map(w=>`<option value="${w.id}">${esc(w.code)} - ${esc(w.name)}</option>`).join("");
    $("#trTo").innerHTML=warehouses.map(w=>`<option value="${w.id}">${esc(w.code)} - ${esc(w.name)}</option>`).join("");
    $("#trProduct").innerHTML=products.map(p=>`<option value="${p.id}">${esc(p.code)} - ${esc(p.name)}</option>`).join("");
  }
  if(can("lots")){
    $("#lotWarehouse").innerHTML=whAllowed.map(w=>`<option value="${w.id}">${esc(w.code)} - ${esc(w.name)}</option>`).join("");
    $("#lotProduct").innerHTML=products.map(p=>`<option value="${p.id}">${esc(p.code)} - ${esc(p.name)}</option>`).join("");
  }
}
function renderProfile(){
  if(!me) return;
  $("#profileBox").innerHTML=`<h3>${esc(me.name)}</h3><p><b>Email:</b> ${esc(me.email)}</p><p><b>Tên đăng nhập:</b> ${esc(me.username||"")}</p><p><b>Vai trò:</b> ${esc(me.role)}</p><p><b>Kho phụ trách:</b> ${esc(me.warehouse_code||"—")}</p><p class="muted">${esc(roleText[me.role]||"")}</p>`;
}

$("#showProductForm").onclick=()=>$("#productFormBox").classList.toggle("hidden");
$("#productForm").onsubmit=async e=>{
  e.preventDefault();
  try{
    await req("/products",{method:"POST",body:JSON.stringify({user_id:me.id,code:$("#pCode").value,name:$("#pName").value,category_id:$("#pCategory").value||null,unit:$("#pUnit").value,price:+$("#pPrice").value,sale_price:+$("#pSale").value,min_stock:+$("#pMin").value,barcode:$("#pBarcode").value,description:$("#pDescription").value})});
    e.target.reset();$("#productFormBox").classList.add("hidden");toast("Đã thêm hàng hóa.");loadAll();
  }catch(x){toast(x.message)}
};
$("#categoryForm").onsubmit=async e=>{e.preventDefault();try{await req("/categories",{method:"POST",body:JSON.stringify({user_id:me.id,code:$("#cCode").value,name:$("#cName").value,description:$("#cDescription").value})});e.target.reset();toast("Đã thêm danh mục.");loadAll()}catch(x){toast(x.message)}};
$("#warehouseForm").onsubmit=async e=>{e.preventDefault();try{await req("/warehouses",{method:"POST",body:JSON.stringify({user_id:me.id,code:$("#wCode").value,name:$("#wName").value,address:$("#wAddress").value})});e.target.reset();toast("Đã thêm kho.");loadAll()}catch(x){toast(x.message)}};
$("#supplierForm").onsubmit=async e=>{e.preventDefault();try{await req("/suppliers",{method:"POST",body:JSON.stringify({user_id:me.id,code:$("#sCode").value,name:$("#sName").value,phone:$("#sPhone").value,email:$("#sEmail").value,address:$("#sAddress").value})});e.target.reset();toast("Đã thêm nhà cung cấp.");loadAll()}catch(x){toast(x.message)}};
$("#txForm").onsubmit=async e=>{e.preventDefault();try{await req("/transactions",{method:"POST",body:JSON.stringify({user_id:me.id,type:$("#txType").value,warehouse_id:+$("#txWarehouse").value,product_id:+$("#txProduct").value,supplier_id:$("#txSupplier").value?+$("#txSupplier").value:null,quantity:+$("#txQty").value,price:+$("#txPrice").value,note:$("#txNote").value})});toast("Đã tạo phiếu chờ xác nhận.");loadAll()}catch(x){toast(x.message)}};
window.confirmTx=async id=>{try{await req(`/transactions/${id}/confirm`,{method:"POST",body:JSON.stringify({user_id:me.id})});toast("Đã xác nhận và cập nhật tồn kho.");loadAll()}catch(x){toast(x.message)}};
window.cancelTx=async id=>{try{await req(`/transactions/${id}/cancel`,{method:"POST",body:JSON.stringify({user_id:me.id})});toast("Đã hủy phiếu.");loadAll()}catch(x){toast(x.message)}};

$("#transferForm").onsubmit=async e=>{e.preventDefault();try{await req("/transfers",{method:"POST",body:JSON.stringify({user_id:me.id,from_warehouse_id:+$("#trFrom").value,to_warehouse_id:+$("#trTo").value,product_id:+$("#trProduct").value,quantity:+$("#trQty").value,note:$("#trNote").value})});toast("Đã tạo phiếu chuyển kho.");loadAll()}catch(x){toast(x.message)}};
window.confirmTransfer=async id=>{try{await req(`/transfers/${id}/confirm`,{method:"POST",body:JSON.stringify({user_id:me.id})});toast("Đã xác nhận chuyển kho.");loadAll()}catch(x){toast(x.message)}};
window.cancelTransfer=async id=>{try{await req(`/transfers/${id}/cancel`,{method:"POST",body:JSON.stringify({user_id:me.id})});toast("Đã hủy phiếu chuyển.");loadAll()}catch(x){toast(x.message)}};

$("#lotForm").onsubmit=async e=>{e.preventDefault();try{await req("/lots",{method:"POST",body:JSON.stringify({user_id:me.id,code:$("#lotCode").value,warehouse_id:+$("#lotWarehouse").value,product_id:+$("#lotProduct").value,quantity:+$("#lotQty").value,received_date:$("#lotReceived").value||null,expiry_date:$("#lotExpiry").value||null})});e.target.reset();toast("Đã thêm lô hàng.");loadAll()}catch(x){toast(x.message)}};

$("#scanForm").onsubmit=async e=>{e.preventDefault();try{const j=await req(`/scan?user_id=${me.id}&q=${encodeURIComponent($("#scanInput").value)}`);const x=j.data;if(!x){$("#scanResult").textContent="Không tìm thấy hàng hóa.";return}$("#scanResult").innerHTML=`<h3>${esc(x.code)} - ${esc(x.name)}</h3><p><b>Mã vạch:</b> ${esc(x.barcode||"—")} · <b>Danh mục:</b> ${esc(x.category||"—")}</p><p><b>Giá:</b> ${money(x.price)} · <b>Tồn tối thiểu:</b> ${x.min_stock}</p><h4>Tồn theo kho</h4>${(x.stock||[]).map(s=>`<div>${esc(s.warehouse_code)} - ${esc(s.warehouse_name)}: <b>${s.quantity}</b></div>`).join("")||"<div>Chưa có tồn.</div>"}`;}catch(x){toast(x.message)}};

async function loadAlerts(){if(!can("alerts"))return;try{alerts=(await req(`/alerts?user_id=${me.id}`)).data||[];renderAlerts()}catch(x){toast(x.message)}}
async function loadReports(){
  if(!can("reports"))return;
  try{
    const d=(await req(`/reports?user_id=${me.id}`)).data||{};
    $("#rValue").textContent=money(d.inventory_value);$("#rImport").textContent=money(d.import_value);$("#rExport").textContent=money(d.export_value);$("#rConfirmed").textContent=d.confirmed_count||0;
    $("#monthlyReport").innerHTML=(d.monthly||[]).map(x=>`<div><b>${esc(x.month)}</b><br><span class="muted">Nhập: ${money(x.import_value)} · Xuất: ${money(x.export_value)}</span></div>`).join("")||"<div class='muted'>Chưa có dữ liệu.</div>";
    $("#supplierReport").innerHTML=(d.suppliers||[]).map(x=>`<div><b>${esc(x.supplier_name)}</b><br><span class="muted">${money(x.value)} · ${x.receipts} phiếu nhập</span></div>`).join("")||"<div class='muted'>Chưa có dữ liệu.</div>";
  }catch(x){toast(x.message)}
}
async function loadAudit(){try{const j=await req(`/audit-logs?user_id=${me.id}`);$("#auditRows").innerHTML=(j.data||[]).map(x=>`<tr><td>${esc(x.created_at)}</td><td>${esc(x.user_name||"Hệ thống")}</td><td>${esc(x.action)}</td><td>${esc(x.target||"—")}</td><td>${esc(x.detail||"")}</td></tr>`).join("")||`<tr><td colspan="5">Chưa có nhật ký.</td></tr>`}catch(x){toast(x.message)}}
async function loadAccounts(){try{const j=await req(`/accounts?user_id=${me.id}`);const a=j.data||[];$("#adminAccount").innerHTML=a.map(x=>`<option value="${x.id}">${esc(x.name)} — ${esc(x.role)} — ${esc(x.email)}</option>`).join("");$("#accountRows").innerHTML=a.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.email)}</td><td>${esc(x.role)}</td><td>${esc(x.warehouse_code||"—")}</td><td><span class="badge ${x.active?"ok":"bad"}">${x.active?"Hoạt động":"Đã khóa"}</span></td></tr>`).join("")||`<tr><td colspan="5">Chưa có tài khoản.</td></tr>`}catch(x){toast(x.message)}}
$("#adminPasswordForm").onsubmit=async e=>{e.preventDefault();if($("#adminNewPassword").value!==$("#adminNewPassword2").value){toast("Mật khẩu nhập lại không khớp.");return}try{await req(`/accounts/${Number($("#adminAccount").value)}/set-password`,{method:"POST",body:JSON.stringify({admin_id:me.id,password:$("#adminNewPassword").value})});e.target.reset();toast("Đã cấp mật khẩu mới.");loadAccounts();loadAudit()}catch(x){toast(x.message)}};

const suggestionByRole={
  Admin:["Tóm tắt tình hình toàn bộ hệ thống","Mặt hàng nào sắp hết?","Kho nào có giá trị tồn lớn nhất?","Phiếu nào đang chờ xác nhận?","Lô nào sắp hết hạn?","Nhà cung cấp nào có giá trị nhập cao nhất?","So sánh nhập và xuất 30 ngày gần đây","Đề xuất mặt hàng cần nhập thêm","Có chuyển kho nào đang chờ không?","Tổng giá trị tồn kho hiện tại?","Liệt kê 10 hoạt động gần nhất","Giải thích C01 đến C09"],
  "Thủ kho":["Tồn kho của kho tôi hiện tại?","Mặt hàng nào sắp hết trong kho tôi?","Phiếu nhập xuất nào đang chờ?","Tôi có thể xuất SP003 bao nhiêu?","Lô nào sắp hết hạn?","Có phiếu chuyển kho nào đang chờ?","Đề xuất nhập thêm hàng cho kho tôi","Tóm tắt hoạt động kho hôm nay"],
  "Kế toán":["Giá trị tồn kho hiện tại?","Tổng giá trị nhập đã xác nhận?","Tổng giá trị xuất đã xác nhận?","So sánh nhập và xuất theo tháng","Nhà cung cấp nào có giá trị nhập cao nhất?","Có bao nhiêu phiếu đã xác nhận?","Tóm tắt báo cáo kho cho kế toán","Phân tích xu hướng nhập xuất gần đây"]
};
function setupSuggestions(){
  const a=suggestionByRole[me.role]||[];
  $("#aiSuggestions").innerHTML=a.map(q=>`<button class="suggestion" type="button">${esc(q)}</button>`).join("");
  $$("#aiSuggestions .suggestion").forEach(b=>b.onclick=()=>{$("#chatInput").value=b.textContent;$("#chatInput").focus()});
}
$("#chatForm").onsubmit=async e=>{
  e.preventDefault();const q=$("#chatInput").value.trim();if(!q)return;
  $("#messages").insertAdjacentHTML("beforeend",`<div class="msg user">${esc(q)}</div>`);$("#chatInput").value="";
  chatHistory.push({role:"user",text:q});chatHistory=chatHistory.slice(-12);
  try{
    const j=await req("/chat",{method:"POST",body:JSON.stringify({user_id:me.id,message:q,history:chatHistory})});
    const ans=j.answer||"Chưa có câu trả lời.";
    $("#messages").insertAdjacentHTML("beforeend",`<div class="msg ai">${esc(ans)}</div>`);
    chatHistory.push({role:"assistant",text:ans});chatHistory=chatHistory.slice(-12);
  }catch(x){$("#messages").insertAdjacentHTML("beforeend",`<div class="msg ai">Lỗi: ${esc(x.message)}</div>`)}
  $("#messages").scrollTop=$("#messages").scrollHeight;
};

const saved=sessionStorage.getItem("khoai_user");
if(saved){try{me=JSON.parse(saved);start()}catch{sessionStorage.removeItem("khoai_user")}}

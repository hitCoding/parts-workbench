export interface Slot {id:string;box_id:string;row_no:number;col_no:number}
export interface Box {id:string;code:string;name:string;area:string;rows:number;cols:number;slots:Slot[]}
export interface Stock {id:string;part_id:string;slot_id:string;quantity:number|null;mode:string;box_code:string;box_name:string;row_no:number;col_no:number;area:string}
export interface Part {product_name?:string;id:string;name:string;category:string;package:string;value:string;specs:Record<string,string>;supplier_code:string;notes:string;archived:number;locations:Stock[];exact:number;available:number;reserved:number}
export interface Project {id:string;name:string;boards:number;status:string;created_at:string}
export interface BomLine {supply_mode:'stock'|'omit'|'external';supply_note:string;match_notes?:string[];unmatched_reason?:string;missing_specs?:string[];supplier_code?:string;source_package?:string;warnings?:string[];id:string;line_no:number;name:string;category:string;package:string;value:string;specs_data:Record<string,string>;per_board:number;designators:string;part:Part|null;candidates:Part[];demand:number;shortage:number|null;available_before:number;match:string;status:string;reason:string}
export interface ProjectDetail extends Project {stock_lines:number;omitted:number;external:number;source?:{format?:string;filename?:string;sheet?:string};lines:BomLine[];shortages:{part:Part;quantity:number}[];unresolved:number;uncertain:number;ready:boolean}
export interface Purchase {product_name?:string;order_line_no?:number;order_id?:string;source_shipped?:number|null;id:string;part_id:string;project_id:string|null;part_name:string;package:string;specs:string;project_name:string|null;quantity:number;received:number;status:string;supplier:string;notes:string;created_at:string}
export interface Movement {id:string;part_name:string;kind:string;before_qty:number|null;after_qty:number|null;delta:number|null;note:string;created_at:string;box_code:string;row_no:number;col_no:number}
export interface State {purchase_orders?:PurchaseOrder[];parts:Part[];boxes:Box[];projects:Project[];purchases:Purchase[];movements:Movement[];storage:{database:string;backups:string;schema_version:number}}
export interface Parsed {ignored_columns?:string[];format?:string;sheet?:string;header_row?:number;project_name?:string;headers:string[];rows:Record<string,string>[];mapping:Record<string,string>;filename:string}
export interface PreviewRow {warnings?:string[];supplier_code?:string;name:string;category:string;package:string;quantity:number|null;row_no:number;errors:string[];reuse_id?:string}
export interface User{id:string;username:string;display_name:string;role:'admin'|'editor'|'viewer';active:number}

export interface PurchaseOrder{archived?:boolean;archived_at?:string;order_id:string;filename:string;purchase_ids:string[];shipped:Record<string,number|null>;created_at:string}

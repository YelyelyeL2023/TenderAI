"""GraphQL queries used for ingestion. Field lists were confirmed by schema introspection
(tests/fixtures/ows/graphql_schema.json)."""

PLANS = """
query($limit:Int, $after:Int, $filter:PlansFiltersInput) {
  Plans(limit:$limit, after:$after, filter:$filter) {
    id rootrecordId sysSubjectsId subjectBiin subjectNameRu subjectNameKz
    nameRu nameKz descRu descKz extraDescRu extraDescKz
    refEnstruCode refEnstruId refUnitsCode count price amount
    refMonthsId plnPointYear refPlnPointStatusId refTradeMethodsId refSubjectTypeId refFinsourceId
    refPointTypeId refJustificationId supplyDateRu prepayment createdinActId activeActId
    isQvazi isActive isDeleted dateCreate timestamp systemId indexDate
    PlansKato { id refKatoCode refCountriesCode fullDeliveryPlaceNameRu fullDeliveryPlaceNameKz count isActive isDeleted }
    RefEnstru { id code nameRu nameKz parentId level edizm mkei }
  }
}"""

TRD_BUY = """
query($limit:Int, $after:Int, $filter:TrdBuyFiltersInput) {
  TrdBuy(limit:$limit, after:$after, filter:$filter) {
    id numberAnno nameRu nameKz totalSum countLots refTradeMethodsId refSubjectTypeId
    customerBin customerPid customerNameRu customerNameKz orgBin orgPid orgNameRu orgNameKz
    refBuyStatusId startDate endDate publishDate itogiDatePublic refTypeTradeId parentId
    idSupplier biinSupplier finYear kato lastUpdateDate systemId indexDate
  }
}"""

LOTS = """
query($limit:Int, $after:Int, $filter:LotsFiltersInput) {
  Lots(limit:$limit, after:$after, filter:$filter) {
    id lotNumber refLotStatusId nameRu nameKz descriptionRu descriptionKz count amount
    customerId customerBin customerNameRu trdBuyId trdBuyNumberAnno
    refTradeMethodsId refBuyTradeMethodsId dumping unionLots
    pointList enstruList plnPointKatoList isDeleted lastUpdateDate systemId indexDate
  }
}"""

CONTRACT = """
query($limit:Int, $after:Int, $filter:ContractFiltersInput) {
  Contract(limit:$limit, after:$after, filter:$filter) {
    id parentId rootId trdBuyId trdBuyNumberAnno trdBuyNameRu contractNumber contractNumberSys
    customerId customerBin supplierId supplierBiin refContractStatusId refContractTypeId deleted
    finYear refCurrencyCode exchangeRate contractSum contractSumWnds withNds
    crdate signDate ecEndDate planExecDate faktExecDate faktSum faktSumWnds contractEndDate
    faktTradeMethodsId refSubjectTypeId descriptionRu descriptionKz lastUpdateDate systemId indexDate
    ContractUnits {
      id lotId plnPointId itemPrice itemPriceWnds quantity totalSum totalSumWnds
      factSum factSumWnds deleted executed refEnstruCode parentId rootId crdate
    }
  }
}"""

SUBJECTS = """
query($limit:Int, $after:Int, $filter:SubjectFiltersInput) {
  Subjects(limit:$limit, after:$after, filter:$filter) {
    pid bin iin nameRu nameKz fullNameRu fullNameKz katoList customer organizer supplier
    typeSupplier krpCode okedList markResident countryCode lastUpdateDate indexDate
    Address { id addressType address katoCode countryCode }
  }
}"""

# root field name, query, id field — used by the generic loader
ENTITIES: dict[str, tuple[str, str, str]] = {
    "plan_point": ("Plans", PLANS, "id"),
    "trd_buy": ("TrdBuy", TRD_BUY, "id"),
    "lot": ("Lots", LOTS, "id"),
    "contract": ("Contract", CONTRACT, "id"),
    "subject": ("Subjects", SUBJECTS, "pid"),
}

# REST dictionaries loaded in full (small) — ENSTRU is not exposed via REST in v3 and comes nested on plans.
REFS: tuple[str, ...] = (
    "ref_units", "ref_kato", "ref_trade_methods", "ref_buy_status", "ref_lots_status",
    "ref_contract_status", "ref_pln_point_status", "ref_subject_type", "ref_months",
    "ref_finsource", "ref_type_trade", "ref_contract_type",
)

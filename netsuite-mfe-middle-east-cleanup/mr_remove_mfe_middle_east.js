/**
 * @NApiVersion 2.1
 * @NScriptType MapReduceScript
 *
 * Removes the MFE Middle East FZCO subsidiary (internal ID 10) from customers
 * whose default billing address (or shipping address, when billing is blank)
 * is in the US, Canada or Mexico.
 *
 * Skips:
 *  - customers where MFE Middle East is the PRIMARY subsidiary
 *  - customers that have any transaction under MFE Middle East
 *
 * Set the script parameter custscript_me_dry_run = T to log only.
 */
define(['N/query', 'N/record', 'N/runtime', 'N/log'], (query, record, runtime, log) => {
  const ME_SUB = 10;

  const getInputData = () => ({
    type: 'suiteql',
    query: `
      SELECT r.id, r.entity
      FROM customersubsidiaryrelationship r
      JOIN customer c ON c.id = r.entity
      LEFT JOIN entityaddress ba ON ba.nkey = c.defaultbillingaddress
      LEFT JOIN entityaddress sa ON sa.nkey = c.defaultshippingaddress
      WHERE r.subsidiary = ${ME_SUB}
        AND r.isprimarysub = 'F'
        AND NVL(ba.country, sa.country) IN ('US', 'CA', 'MX')
        AND r.entity NOT IN (
          SELECT t.entity FROM transaction t
          JOIN transactionline tl ON tl.transaction = t.id AND tl.mainline = 'T'
          WHERE tl.subsidiary = ${ME_SUB} AND t.entity IS NOT NULL
        )`,
    params: []
  });

  const map = (ctx) => {
    const row = JSON.parse(ctx.value).values; // [relId, entityId]
    const relId = row[0];
    const dryRun = runtime.getCurrentScript().getParameter({ name: 'custscript_me_dry_run' });
    if (dryRun === true || dryRun === 'T') {
      log.audit('DRY RUN - would delete', `rel ${relId} customer ${row[1]}`);
      return;
    }
    try {
      record.delete({ type: 'customersubsidiaryrelationship', id: relId });
      ctx.write({ key: 'deleted', value: relId });
    } catch (e) {
      log.error(`Failed rel ${relId} customer ${row[1]}`, e.message);
      ctx.write({ key: 'failed', value: `${relId}:${e.message}` });
    }
  };

  const summarize = (s) => {
    let deleted = 0, failed = 0;
    s.output.iterator().each((k) => { k === 'deleted' ? deleted++ : failed++; return true; });
    log.audit('Done', `deleted=${deleted} failed=${failed}`);
  };

  return { getInputData, map, summarize };
});
